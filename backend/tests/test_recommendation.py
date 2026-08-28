"""FR-10 scoring, §10.3 candidate selection, and FR-13 reason strings."""

import pandas as pd
import pytest

from app.ml.portfolio import constraints as cm
from app.ml.recommendation import candidates as candidate_module
from app.ml.recommendation import reasons as reason_module
from app.ml.recommendation import scoring as scoring_module
from app.models.enums import AssetClass, InvestmentGoal, RiskCategory


def feature(
    symbol: str,
    asset_class: AssetClass,
    expected_return: float,
    volatility: float,
    momentum: float = 0.05,
    confidence: float = 0.5,
) -> scoring_module.AssetFeatures:
    return scoring_module.AssetFeatures(
        symbol=symbol,
        asset_class=asset_class,
        expected_return=expected_return,
        volatility=volatility,
        momentum=momentum,
        confidence=confidence,
    )


@pytest.fixture
def universe() -> list[scoring_module.AssetFeatures]:
    return [
        feature("SPY", AssetClass.EQUITY, 0.10, 0.16),
        feature("QQQ", AssetClass.EQUITY, 0.13, 0.22),
        feature("VTV", AssetClass.EQUITY, 0.08, 0.13),
        feature("XLU", AssetClass.EQUITY, 0.06, 0.11),
        feature("AGG", AssetClass.BOND, 0.03, 0.05),
        feature("TLT", AssetClass.BOND, 0.04, 0.12),
        feature("SHY", AssetClass.BOND, 0.02, 0.02),
        feature("LQD", AssetClass.BOND, 0.035, 0.07),
        feature("GLD", AssetClass.COMMODITY, 0.05, 0.15),
        feature("DBC", AssetClass.COMMODITY, 0.06, 0.18),
        feature("VNQ", AssetClass.REAL_ESTATE, 0.07, 0.19),
    ]


class TestScoring:
    def test_weights_sum_to_one(self) -> None:
        total = (
            scoring_module.WEIGHT_RETURN
            + scoring_module.WEIGHT_VOLATILITY_FIT
            + scoring_module.WEIGHT_MOMENTUM
            + scoring_module.WEIGHT_CONFIDENCE
            + scoring_module.WEIGHT_GOAL_FIT
        )
        assert total == pytest.approx(1.0)

    def test_results_are_ranked_highest_first(self, universe) -> None:
        scored = scoring_module.score_assets(
            universe, risk_category=RiskCategory.MEDIUM, goal=InvestmentGoal.GROWTH
        )
        assert [s.score for s in scored] == sorted((s.score for s in scored), reverse=True)

    def test_risk_class_changes_which_assets_score_well(self, universe) -> None:
        """Volatility is scored on *distance from a target*, not "less is better" —
        a LOW-risk user is not best served by the single least volatile asset, and a
        HIGH-risk one is not best served by the most volatile."""
        low = scoring_module.score_assets(
            universe, risk_category=RiskCategory.LOW, goal=InvestmentGoal.WEALTH_CREATION
        )
        high = scoring_module.score_assets(
            universe, risk_category=RiskCategory.HIGH, goal=InvestmentGoal.WEALTH_CREATION
        )

        low_fit = {s.symbol: s.components["volatility_fit"] for s in low}
        high_fit = {s.symbol: s.components["volatility_fit"] for s in high}

        assert low_fit["SHY"] > high_fit["SHY"]
        assert high_fit["QQQ"] > low_fit["QQQ"]

    def test_the_goal_changes_the_ranking(self, universe) -> None:
        retirement = scoring_module.score_assets(
            universe, risk_category=RiskCategory.MEDIUM, goal=InvestmentGoal.RETIREMENT
        )
        growth = scoring_module.score_assets(
            universe, risk_category=RiskCategory.MEDIUM, goal=InvestmentGoal.GROWTH
        )
        assert [s.symbol for s in retirement] != [s.symbol for s in growth]

    def test_components_sum_to_the_score(self, universe) -> None:
        """The score has to be decomposable, because FR-13's reasons are built from
        the components — a reason derived from a number that does not add up is a
        reason about nothing."""
        scored = scoring_module.score_assets(
            universe, risk_category=RiskCategory.MEDIUM, goal=InvestmentGoal.GROWTH
        )
        for asset in scored:
            assert sum(asset.components.values()) == pytest.approx(asset.score, abs=1e-5)

    def test_an_empty_universe_returns_nothing_rather_than_raising(self) -> None:
        assert (
            scoring_module.score_assets(
                [], risk_category=RiskCategory.LOW, goal=InvestmentGoal.GROWTH
            )
            == []
        )


class TestCandidateSelection:
    def test_it_returns_a_bounded_candidate_set(self, universe) -> None:
        scored = scoring_module.score_assets(
            universe, risk_category=RiskCategory.MEDIUM, goal=InvestmentGoal.GROWTH
        )
        selected = candidate_module.select_candidates(
            scored, risk_category=RiskCategory.MEDIUM, count=6
        )
        assert 0 < len(selected.assets) <= len(universe)

    def test_it_covers_more_than_one_asset_class(self, universe) -> None:
        """Pure score-ranking would happily return six equities. The optimizer cannot
        diversify across a candidate set that is all one class."""
        scored = scoring_module.score_assets(
            universe, risk_category=RiskCategory.HIGH, goal=InvestmentGoal.GROWTH
        )
        selected = candidate_module.select_candidates(
            scored, risk_category=RiskCategory.HIGH, count=6
        )
        classes = {a.features.asset_class for a in selected.assets}
        assert len(classes) > 1

    def test_it_honours_the_constraint_floors(self, universe) -> None:
        """Regression. Selection ran before the constraints were known, so a
        LOW-risk user could get two bonds against a 45% bond floor — infeasible, and
        a 422 for a perfectly reasonable request."""
        risk = RiskCategory.LOW
        bands, _ = cm.build_bands(
            risk_category=risk, goal=InvestmentGoal.WEALTH_CREATION, horizon_years=20
        )
        required = cm.required_asset_counts(bands, cm.MAX_WEIGHT_PER_ASSET[risk])

        scored = scoring_module.score_assets(
            universe, risk_category=risk, goal=InvestmentGoal.WEALTH_CREATION
        )
        selected = candidate_module.select_candidates(
            scored, risk_category=risk, count=6, required_per_class=required
        )

        bonds = sum(1 for a in selected.assets if a.features.asset_class is AssetClass.BOND)
        assert bonds >= required[AssetClass.BOND]

    def test_the_selected_set_yields_a_feasible_constraint_set(self, universe) -> None:
        """The end-to-end version: selection and constraints must agree."""
        risk = RiskCategory.LOW
        bands, _ = cm.build_bands(
            risk_category=risk, goal=InvestmentGoal.RETIREMENT, horizon_years=10
        )
        required = cm.required_asset_counts(bands, cm.MAX_WEIGHT_PER_ASSET[risk])

        scored = scoring_module.score_assets(
            universe, risk_category=risk, goal=InvestmentGoal.RETIREMENT
        )
        selected = candidate_module.select_candidates(
            scored, risk_category=risk, required_per_class=required
        )

        # Must not raise.
        cm.build_constraints(
            risk_category=risk,
            goal=InvestmentGoal.RETIREMENT,
            horizon_years=10,
            asset_classes=[a.features.asset_class for a in selected.assets],
        )

    def test_the_target_reflects_the_risk_class(self, universe) -> None:
        scored = scoring_module.score_assets(
            universe, risk_category=RiskCategory.LOW, goal=InvestmentGoal.GROWTH
        )
        low = candidate_module.select_candidates(scored, risk_category=RiskCategory.LOW)
        high = candidate_module.select_candidates(scored, risk_category=RiskCategory.HIGH)
        assert low.target["volatility"] < high.target["volatility"]


class TestReasons:
    @pytest.fixture
    def scored_asset(self, universe) -> scoring_module.ScoredAsset:
        return scoring_module.score_assets(
            universe, risk_category=RiskCategory.MEDIUM, goal=InvestmentGoal.GROWTH
        )[0]

    def test_a_reason_names_the_asset_and_its_weight(self, scored_asset) -> None:
        """FR-13: at least one plain-language reason, attached before it is shown."""
        reason = reason_module.asset_reason(
            scored_asset,
            weight=0.18,
            risk_category=RiskCategory.MEDIUM,
            goal=InvestmentGoal.GROWTH,
        )
        assert scored_asset.symbol in reason
        assert "18.0%" in reason

    def test_a_reason_quotes_the_figures_it_was_derived_from(self, scored_asset) -> None:
        """The rule this module lives by: a reason must be derived from the numbers
        that drove the decision, never composed to sound convincing."""
        reason = reason_module.asset_reason(
            scored_asset,
            weight=0.2,
            risk_category=RiskCategory.MEDIUM,
            goal=InvestmentGoal.GROWTH,
        )
        assert f"{scored_asset.features.volatility:.1%}" in reason
        assert f"{scored_asset.features.expected_return:.1%}" in reason

    def test_reasons_differ_between_assets(self, universe) -> None:
        scored = scoring_module.score_assets(
            universe, risk_category=RiskCategory.MEDIUM, goal=InvestmentGoal.GROWTH
        )
        reasons = {
            reason_module.asset_reason(
                asset, weight=0.1, risk_category=RiskCategory.MEDIUM, goal=InvestmentGoal.GROWTH
            )
            for asset in scored[:5]
        }
        assert len(reasons) == 5

    def test_a_reason_makes_no_promise_about_outcomes(self, universe) -> None:
        """§5 and §17.2 — this system does not get to claim what will happen."""
        scored = scoring_module.score_assets(
            universe, risk_category=RiskCategory.MEDIUM, goal=InvestmentGoal.GROWTH
        )
        forbidden = ("will grow", "guaranteed", "will outperform", "you should buy", "safe bet")
        for asset in scored:
            reason = reason_module.asset_reason(
                asset, weight=0.1, risk_category=RiskCategory.MEDIUM, goal=InvestmentGoal.GROWTH
            ).lower()
            for phrase in forbidden:
                assert phrase not in reason

    def test_the_portfolio_summary_lists_the_constraints_that_shaped_it(self) -> None:
        """ "Why only 12% equities?" is answered by the band in force, not by the
        objective value."""
        summary = reason_module.portfolio_summary(
            risk_category=RiskCategory.LOW,
            goal=InvestmentGoal.RETIREMENT,
            horizon_years=8,
            expected_return=0.05,
            expected_risk=0.07,
            constraint_notes=["Equity capped at 25%.", "Bonds floored at 45%."],
            holdings=6,
        )
        assert "Equity capped at 25%." in summary
        assert "not selected from a preset allocation" in summary
        assert "not forecasts" in summary


class TestRankingMetrics:
    """§18's Precision@K / Recall@K / NDCG.

    Relevance is rule-derived — see `app/ml/recommendation/evaluation.py`. These
    tests check the arithmetic, not the quality of the ranker.
    """

    def test_a_perfect_ranking_scores_one(self) -> None:
        from app.ml.recommendation import evaluation as rank_eval

        assets = [
            feature("A", AssetClass.EQUITY, 0.12, 0.14),
            feature("B", AssetClass.EQUITY, 0.10, 0.14),
            feature("C", AssetClass.BOND, 0.02, 0.90),  # far outside any band
        ]
        ranked = scoring_module.score_assets(
            assets, risk_category=RiskCategory.MEDIUM, goal=InvestmentGoal.GROWTH
        )
        metrics = rank_eval.evaluate_ranking(
            ranked, risk_category=RiskCategory.MEDIUM, goal=InvestmentGoal.GROWTH, k=2
        )
        assert metrics.precision_at_k == pytest.approx(1.0)
        assert metrics.ndcg_at_k == pytest.approx(1.0)

    def test_no_relevant_assets_gives_zero_rather_than_dividing_by_zero(self) -> None:
        from app.ml.recommendation import evaluation as rank_eval

        assets = [feature("X", AssetClass.BOND, 0.02, 0.90)]
        ranked = scoring_module.score_assets(
            assets, risk_category=RiskCategory.HIGH, goal=InvestmentGoal.GROWTH
        )
        metrics = rank_eval.evaluate_ranking(
            ranked, risk_category=RiskCategory.HIGH, goal=InvestmentGoal.GROWTH
        )
        assert metrics.relevant_total == 0
        assert metrics.recall_at_k == 0.0
        assert metrics.ndcg_at_k == 0.0

    def test_ndcg_rewards_putting_relevant_items_first(self) -> None:
        from app.ml.recommendation import evaluation as rank_eval

        good = rank_eval._ndcg([1.0, 0.0, 0.0], [1.0, 0.0, 0.0], 3)
        bad = rank_eval._ndcg([0.0, 0.0, 1.0], [0.0, 0.0, 1.0], 3)
        assert good > bad

    def test_relevance_is_not_the_scoring_function(self, universe) -> None:
        """Deliberate: if relevance were the scorer, the metric would be a
        tautology. They must be able to disagree."""
        from app.ml.recommendation import evaluation as rank_eval

        ranked = scoring_module.score_assets(
            universe, risk_category=RiskCategory.LOW, goal=InvestmentGoal.RETIREMENT
        )
        top_by_score = {a.symbol for a in ranked[:4]}
        relevant = {
            a.symbol
            for a in ranked
            if rank_eval.is_relevant(
                a.features, risk_category=RiskCategory.LOW, goal=InvestmentGoal.RETIREMENT
            )
        }
        assert top_by_score != relevant


class TestSyntheticUniverseFidelity:
    """Regression: the offline universe must be able to represent every risk class.

    Every synthetic asset originally used one volatility distribution, so a treasury
    ETF was as volatile as a growth stock (~19% annualised across the board). The
    risk classes target 8% / 14% / 22%, so a LOW-risk profile had no suitable asset
    anywhere offline — and §18's metrics reported zero relevant assets for every LOW
    combination. A universe that cannot represent a conservative portfolio cannot
    exercise the conservative path.
    """

    def test_bonds_are_materially_less_volatile_than_equities(self) -> None:
        from datetime import date

        import numpy as np

        from app.ml.features import technical
        from app.providers.synthetic import SyntheticMarketDataProvider

        provider = SyntheticMarketDataProvider()
        window = (date(2024, 1, 1), date(2026, 1, 1))

        def annualised(symbol: str) -> float:
            bars = provider.fetch_daily_bars(symbol, *window)
            closes = pd.Series([float(bar.close) for bar in bars])
            return float(technical.log_returns(closes).std(ddof=0) * np.sqrt(252))

        bond = annualised("SHY")
        equity = annualised("QQQ")

        assert bond < 0.08, f"bond volatility {bond:.3f} is too high to be a bond"
        assert equity > 0.12, f"equity volatility {equity:.3f} is too low to be an equity"
        assert equity > bond * 2

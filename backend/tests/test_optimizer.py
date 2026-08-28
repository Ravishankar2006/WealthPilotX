"""FR-11 — the optimizer and its constraints.

FR-11's two acceptance criteria are the spine of this file: weights sum to 1.0 ±
0.001 with no position over its cap, and an infeasible constraint set produces a
clear error rather than a malformed portfolio.
"""

import numpy as np
import pytest

from app.ml.portfolio import constraints as cm
from app.ml.portfolio import optimizer as om
from app.models.enums import AssetClass, InvestmentGoal, RiskCategory

# Three bonds, not two: a LOW-risk mandate has a 45% bond floor against a 20%
# per-asset cap, so two bonds can carry at most 40% and the set is genuinely
# infeasible. That is a real constraint on what a candidate set must contain, and
# `constraints.required_asset_counts` is what enforces it in production.
CLASSES = (
    AssetClass.EQUITY,
    AssetClass.EQUITY,
    AssetClass.EQUITY,
    AssetClass.BOND,
    AssetClass.BOND,
    AssetClass.BOND,
    AssetClass.COMMODITY,
)


@pytest.fixture
def market() -> tuple[np.ndarray, np.ndarray]:
    """Seven assets: three equities, three bonds, one commodity."""
    mu = np.array([0.11, 0.09, 0.13, 0.03, 0.04, 0.035, 0.06])
    volatility = np.array([0.18, 0.16, 0.24, 0.05, 0.06, 0.04, 0.20])
    correlation = np.full((7, 7), 0.25)
    np.fill_diagonal(correlation, 1.0)
    sigma = np.outer(volatility, volatility) * correlation
    return mu, sigma


def constraints_for(
    risk: RiskCategory,
    goal: InvestmentGoal = InvestmentGoal.WEALTH_CREATION,
    horizon: int = 20,
) -> cm.ConstraintSet:
    return cm.build_constraints(
        risk_category=risk, goal=goal, horizon_years=horizon, asset_classes=list(CLASSES)
    )


SYMBOLS = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG")


class TestAcceptanceCriteria:
    @pytest.mark.parametrize("risk", list(RiskCategory))
    def test_weights_sum_to_one_within_tolerance(self, market, risk: RiskCategory) -> None:
        """FR-11 criterion 1, verbatim: 1.0 ± 0.001."""
        mu, sigma = market
        result = om.optimize(mu, sigma, constraints_for(risk), SYMBOLS)
        assert abs(result.weights.sum() - 1.0) <= 0.001

    @pytest.mark.parametrize("risk", list(RiskCategory))
    def test_no_position_exceeds_the_cap(self, market, risk: RiskCategory) -> None:
        """FR-11 criterion 1, second half."""
        mu, sigma = market
        constraints = constraints_for(risk)
        result = om.optimize(mu, sigma, constraints, SYMBOLS)
        assert result.weights.max() <= constraints.max_weight + 0.001

    def test_weights_are_never_negative(self, market) -> None:
        """Long-only: a negative weight is a short position, and shorting is not
        something an educational tool should be modelling."""
        mu, sigma = market
        result = om.optimize(mu, sigma, constraints_for(RiskCategory.MEDIUM), SYMBOLS)
        assert (result.weights >= 0).all()

    def test_infeasible_caps_are_reported_not_solved(self) -> None:
        """FR-11 criterion 2: a clear error rather than a malformed portfolio."""
        with pytest.raises(cm.InfeasibleConstraintsError) as caught:
            cm.check_feasible(
                cm.ConstraintSet(
                    risk_aversion=5.0,
                    max_weight=0.25,
                    class_bands={
                        AssetClass.EQUITY: (0.0, 0.30),
                        AssetClass.BOND: (0.0, 0.30),
                    },
                    class_of=(AssetClass.EQUITY, AssetClass.BOND),
                )
            )
        assert "impossible" in caught.value.message
        assert caught.value.detail

    def test_floors_that_exceed_one_are_rejected(self) -> None:
        with pytest.raises(cm.InfeasibleConstraintsError, match="exceeds 100%"):
            cm.check_feasible(
                cm.ConstraintSet(
                    risk_aversion=5.0,
                    max_weight=1.0,
                    class_bands={
                        AssetClass.EQUITY: (0.7, 1.0),
                        AssetClass.BOND: (0.6, 1.0),
                    },
                    class_of=(AssetClass.EQUITY, AssetClass.BOND),
                )
            )

    def test_a_universe_too_small_to_fill_the_portfolio_is_rejected(self) -> None:
        """One equity and one bond, each capped at 20%, can hold 40% between them.
        The message must say what is wrong — 'infeasible' alone is unactionable."""
        with pytest.raises(cm.InfeasibleConstraintsError) as caught:
            cm.build_constraints(
                risk_category=RiskCategory.LOW,
                goal=InvestmentGoal.RETIREMENT,
                horizon_years=20,
                asset_classes=[AssetClass.EQUITY, AssetClass.BOND],
            )
        assert "at most" in caught.value.message
        assert caught.value.detail["reachable_total"] < 1.0

    def test_a_floor_no_available_asset_can_meet_is_named(self) -> None:
        """With capacity satisfied, an unmeetable floor still reports its own class."""
        with pytest.raises(cm.InfeasibleConstraintsError) as caught:
            cm.check_feasible(
                cm.ConstraintSet(
                    risk_aversion=12.0,
                    max_weight=0.20,
                    class_bands={
                        AssetClass.EQUITY: (0.0, 0.90),
                        AssetClass.BOND: (0.45, 0.80),
                    },
                    # Six equities give ample capacity; the single bond cannot meet
                    # a 45% floor at a 20% cap.
                    class_of=(AssetClass.EQUITY,) * 6 + (AssetClass.BOND,),
                )
            )
        assert "BOND" in caught.value.message


class TestRiskCategoryChangesTheAnswer:
    def test_a_low_risk_portfolio_is_less_volatile_than_a_high_risk_one(self, market) -> None:
        """The whole justification for risk-aversion-by-category rather than one
        portfolio rescaled: the answers must genuinely differ."""
        mu, sigma = market
        low = om.optimize(mu, sigma, constraints_for(RiskCategory.LOW), SYMBOLS)
        high = om.optimize(mu, sigma, constraints_for(RiskCategory.HIGH), SYMBOLS)

        assert low.expected_risk < high.expected_risk
        assert low.expected_return < high.expected_return

    def test_allocations_differ_materially_not_just_in_scale(self, market) -> None:
        mu, sigma = market
        low = om.optimize(mu, sigma, constraints_for(RiskCategory.LOW), SYMBOLS)
        high = om.optimize(mu, sigma, constraints_for(RiskCategory.HIGH), SYMBOLS)

        equity = [i for i, c in enumerate(CLASSES) if c is AssetClass.EQUITY]
        assert high.weights[equity].sum() > low.weights[equity].sum() + 0.15

    def test_the_goal_shifts_the_equity_band(self) -> None:
        retirement = constraints_for(RiskCategory.MEDIUM, InvestmentGoal.RETIREMENT)
        growth = constraints_for(RiskCategory.MEDIUM, InvestmentGoal.GROWTH)
        assert growth.class_bands[AssetClass.EQUITY] > retirement.class_bands[AssetClass.EQUITY]

    def test_a_short_horizon_tightens_equity(self) -> None:
        """Under five years, volatility stops being a fluctuation and starts being a
        realised loss — regardless of stated appetite."""
        long_horizon = constraints_for(RiskCategory.HIGH, horizon=30)
        short_horizon = constraints_for(RiskCategory.HIGH, horizon=2)
        assert (
            short_horizon.class_bands[AssetClass.EQUITY][1]
            < long_horizon.class_bands[AssetClass.EQUITY][1]
        )


class TestSolverBehaviour:
    def test_it_is_deterministic(self, market) -> None:
        mu, sigma = market
        constraints = constraints_for(RiskCategory.MEDIUM)
        first = om.optimize(mu, sigma, constraints, SYMBOLS)
        second = om.optimize(mu, sigma, constraints, SYMBOLS)
        assert np.allclose(first.weights, second.weights)

    def test_class_bands_are_respected(self, market) -> None:
        mu, sigma = market
        constraints = constraints_for(RiskCategory.LOW)
        result = om.optimize(mu, sigma, constraints, SYMBOLS)

        for cls, (floor, cap) in constraints.class_bands.items():
            indices = [i for i, c in enumerate(CLASSES) if c is cls]
            if not indices:
                continue
            total = result.weights[indices].sum()
            assert total <= cap + 0.01, f"{cls} at {total:.4f} exceeds cap {cap}"
            assert total >= floor - 0.01, f"{cls} at {total:.4f} below floor {floor}"

    def test_misaligned_inputs_raise_rather_than_silently_truncating(self, market) -> None:
        mu, sigma = market
        with pytest.raises(om.OptimizationFailedError, match="misaligned"):
            om.optimize(mu[:3], sigma, constraints_for(RiskCategory.MEDIUM), SYMBOLS)

    def test_tiny_positions_are_trimmed_and_the_rest_renormalised(self, market) -> None:
        """A 0.04% holding is rounding noise, not an allocation — and nobody wants a
        reason string for it."""
        mu, sigma = market
        result = om.optimize(mu, sigma, constraints_for(RiskCategory.MEDIUM), SYMBOLS)
        held = [w for w in result.weights if w > 0]
        assert all(w >= om.MIN_MEANINGFUL_WEIGHT * 0.99 for w in held)
        assert abs(sum(held) - 1.0) <= 0.001

    def test_expected_risk_is_the_portfolio_standard_deviation(self, market) -> None:
        mu, sigma = market
        result = om.optimize(mu, sigma, constraints_for(RiskCategory.MEDIUM), SYMBOLS)
        expected = float(np.sqrt(result.weights @ sigma @ result.weights))
        assert result.expected_risk == pytest.approx(expected, abs=1e-9)


class TestNoStaticAllocationTable:
    def test_weights_respond_to_the_return_vector(self, market) -> None:
        """FR-10's acceptance criterion: weights come from the optimizer, not a
        lookup table. If they were a preset, changing μ would change nothing."""
        mu, sigma = market
        constraints = constraints_for(RiskCategory.MEDIUM)

        base = om.optimize(mu, sigma, constraints, SYMBOLS)
        shifted = mu.copy()
        shifted[1] += 0.10  # make the second asset far more attractive
        moved = om.optimize(shifted, sigma, constraints, SYMBOLS)

        assert moved.weights[1] > base.weights[1] + 0.01
        assert not np.allclose(base.weights, moved.weights)

    def test_weights_respond_to_the_covariance(self, market) -> None:
        mu, sigma = market
        constraints = constraints_for(RiskCategory.MEDIUM)

        base = om.optimize(mu, sigma, constraints, SYMBOLS)
        riskier = sigma.copy()
        riskier[2, 2] *= 4.0  # make the third asset much riskier
        moved = om.optimize(mu, riskier, constraints, SYMBOLS)

        assert moved.weights[2] < base.weights[2]


class TestRequiredAssetCounts:
    """Regression: the candidate set must be able to satisfy its own constraints."""

    def test_a_floor_implies_a_minimum_asset_count(self) -> None:
        bands, _ = cm.build_bands(
            risk_category=RiskCategory.LOW,
            goal=InvestmentGoal.WEALTH_CREATION,
            horizon_years=20,
        )
        required = cm.required_asset_counts(bands, cm.MAX_WEIGHT_PER_ASSET[RiskCategory.LOW])

        # 45% bond floor at a 20% cap needs at least three holdings. It may ask for
        # more to make the portfolio fillable — that is the capacity requirement,
        # tested separately.
        assert required[AssetClass.BOND] >= 3

    def test_no_floor_means_no_requirement(self) -> None:
        bands, _ = cm.build_bands(
            risk_category=RiskCategory.HIGH,
            goal=InvestmentGoal.GROWTH,
            horizon_years=30,
        )
        required = cm.required_asset_counts(bands, cm.MAX_WEIGHT_PER_ASSET[RiskCategory.HIGH])
        assert required[AssetClass.COMMODITY] == 0

    def test_the_requirement_makes_the_constraint_set_feasible(self) -> None:
        """The end-to-end point: supply the required counts and the set solves."""
        risk = RiskCategory.LOW
        bands, _ = cm.build_bands(
            risk_category=risk, goal=InvestmentGoal.WEALTH_CREATION, horizon_years=20
        )
        required = cm.required_asset_counts(bands, cm.MAX_WEIGHT_PER_ASSET[risk])

        classes: list[AssetClass] = []
        for cls, count in required.items():
            classes.extend([cls] * max(count, 1))

        # Must not raise.
        cm.build_constraints(
            risk_category=risk,
            goal=InvestmentGoal.WEALTH_CREATION,
            horizon_years=20,
            asset_classes=classes,
        )


class TestCapacityAccounting:
    """Regression: the per-asset cap limits how much a class can actually hold.

    `check_feasible` originally summed the nominal class caps. A LOW-risk mandate
    allows bonds up to 80% and caps equities at 10%, which sums above 100% and
    passed — but with only three bonds at a 20% per-asset cap, bonds top out at 60%
    and the reachable total was 90%. The set was infeasible, the check said nothing,
    and the solver failed with "Positive directional derivative for linesearch". A
    perfectly ordinary retiree profile got a 422.
    """

    def test_the_effective_cap_accounts_for_the_per_asset_limit(self) -> None:
        with pytest.raises(cm.InfeasibleConstraintsError) as caught:
            cm.check_feasible(
                cm.ConstraintSet(
                    risk_aversion=12.0,
                    max_weight=0.20,
                    class_bands={
                        AssetClass.EQUITY: (0.0, 0.10),
                        AssetClass.BOND: (0.45, 0.80),
                    },
                    # Three bonds can hold 60%, one equity 10% — 70% total.
                    class_of=(
                        AssetClass.BOND,
                        AssetClass.BOND,
                        AssetClass.BOND,
                        AssetClass.EQUITY,
                    ),
                )
            )
        assert "at most" in caught.value.message
        assert caught.value.detail["reachable_total"] < 1.0

    def test_enough_assets_makes_a_workable_band_set_feasible(self) -> None:
        """The asset count, not the bands, is what the top-up fixes.

        Note the equity cap here is 30%, not 10%: with bonds capped at 80% and
        equities at 10%, no asset count reaches 100% — that band set is infeasible
        on its own terms, which is a different problem and a different error.
        """
        cm.check_feasible(
            cm.ConstraintSet(
                risk_aversion=12.0,
                max_weight=0.20,
                class_bands={
                    AssetClass.EQUITY: (0.0, 0.30),
                    AssetClass.BOND: (0.45, 0.80),
                },
                class_of=(AssetClass.BOND,) * 4 + (AssetClass.EQUITY,) * 2,
            )
        )

    def test_the_requirement_asks_for_enough_assets_to_fill_the_portfolio(self) -> None:
        bands, _ = cm.build_bands(
            risk_category=RiskCategory.LOW,
            goal=InvestmentGoal.RETIREMENT,
            horizon_years=4,
        )
        supply = {
            AssetClass.EQUITY: 20,
            AssetClass.BOND: 7,
            AssetClass.COMMODITY: 3,
            AssetClass.REAL_ESTATE: 1,
        }
        required = cm.required_asset_counts(
            bands, cm.MAX_WEIGHT_PER_ASSET[RiskCategory.LOW], available=supply, baseline=2
        )

        reachable = sum(
            min(bands[cls][1], required[cls] * cm.MAX_WEIGHT_PER_ASSET[RiskCategory.LOW])
            for cls in supply
        )
        assert reachable >= 1.0 - 1e-9

    def test_it_never_requires_assets_that_do_not_exist(self) -> None:
        """It once asked for a CASH holding in a universe that tracks none, and
        counted capacity that would never materialise."""
        bands, _ = cm.build_bands(
            risk_category=RiskCategory.LOW,
            goal=InvestmentGoal.RETIREMENT,
            horizon_years=4,
        )
        supply = {AssetClass.EQUITY: 20, AssetClass.BOND: 7, AssetClass.COMMODITY: 3}
        required = cm.required_asset_counts(
            bands, cm.MAX_WEIGHT_PER_ASSET[RiskCategory.LOW], available=supply, baseline=2
        )

        assert required.get(AssetClass.CASH, 0) == 0
        for cls, count in required.items():
            assert count <= supply.get(cls, 0)


class TestFeasibleStart:
    """The solver is local: an infeasible start is why a valid problem failed."""

    def test_the_start_satisfies_the_class_bands(self) -> None:
        classes = (AssetClass.BOND,) * 4 + (AssetClass.EQUITY,) * 5 + (AssetClass.COMMODITY,) * 2
        constraints = cm.build_constraints(
            risk_category=RiskCategory.LOW,
            goal=InvestmentGoal.RETIREMENT,
            horizon_years=4,
            asset_classes=list(classes),
        )
        start = om._feasible_start(constraints, len(classes))

        assert start.sum() == pytest.approx(1.0, abs=1e-6)
        assert start.max() <= constraints.max_weight + 1e-9
        for cls, (floor, cap) in constraints.class_bands.items():
            indices = [i for i, c in enumerate(classes) if c is cls]
            if not indices:
                continue
            total = start[indices].sum()
            assert floor - 1e-6 <= total <= cap + 1e-6

    def test_a_tightly_constrained_profile_still_solves(self) -> None:
        """End to end: LOW risk, retirement goal, short horizon — the combination
        that produced the original failure."""
        classes = (AssetClass.BOND,) * 4 + (AssetClass.EQUITY,) * 5 + (AssetClass.COMMODITY,) * 2
        constraints = cm.build_constraints(
            risk_category=RiskCategory.LOW,
            goal=InvestmentGoal.RETIREMENT,
            horizon_years=4,
            asset_classes=list(classes),
        )

        rng = np.random.default_rng(3)
        n = len(classes)
        mu = rng.uniform(0.02, 0.12, n)
        vol = rng.uniform(0.04, 0.22, n)
        corr = np.full((n, n), 0.2)
        np.fill_diagonal(corr, 1.0)
        sigma = np.outer(vol, vol) * corr

        result = om.optimize(mu, sigma, constraints, tuple(f"S{i}" for i in range(n)))
        assert abs(result.weights.sum() - 1.0) <= 0.001

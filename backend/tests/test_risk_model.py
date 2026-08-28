"""FR-03 — the rubric, the classifier, and the acceptance criteria.

The rubric tests carry more weight than the model tests, and deliberately so: the
model is trained to reproduce the rubric (Phase 3 plan, decision 1), so the rubric is
where the domain judgment actually lives and where a mistake would do real damage.
"""

import dataclasses
from decimal import Decimal

import pytest

from app.ml.risk import dataset, rubric
from app.ml.risk import model as risk_model
from app.models.enums import (
    FinancialLiteracy,
    InvestmentExperience,
    RiskAppetite,
    RiskCategory,
)


def _field_names(artifact: object) -> set[str]:
    """Field names of a slots dataclass. `vars()` raises on these — slots means no
    __dict__ — and that is exactly why the artifacts use them."""
    return {field.name for field in dataclasses.fields(artifact)}  # type: ignore[arg-type]


CONSERVATIVE = {
    "age": 62,
    "income": Decimal("55000"),
    "savings": Decimal("20000"),
    "risk_appetite": RiskAppetite.CONSERVATIVE,
    "investment_horizon": 4,
    "experience": InvestmentExperience.NONE,
    "financial_literacy": FinancialLiteracy.LOW,
}

AGGRESSIVE = {
    "age": 26,
    "income": Decimal("120000"),
    "savings": Decimal("400000"),
    "risk_appetite": RiskAppetite.AGGRESSIVE,
    "investment_horizon": 35,
    "experience": InvestmentExperience.ADVANCED,
    "financial_literacy": FinancialLiteracy.HIGH,
}


class TestRubric:
    def test_weights_sum_to_one(self) -> None:
        total = (
            rubric.WEIGHT_APPETITE
            + rubric.WEIGHT_HORIZON
            + rubric.WEIGHT_AGE
            + rubric.WEIGHT_SAVINGS_RATIO
            + rubric.WEIGHT_EXPERIENCE
            + rubric.WEIGHT_LITERACY
        )
        assert total == pytest.approx(1.0)

    def test_the_extremes_land_where_they_should(self) -> None:
        assert (
            rubric.categorise(rubric.score(rubric.components(**CONSERVATIVE))) is RiskCategory.LOW
        )
        assert rubric.categorise(rubric.score(rubric.components(**AGGRESSIVE))) is RiskCategory.HIGH

    def test_score_is_always_within_bounds(self) -> None:
        for profile in dataset.sample_population(500, seed=1):
            value = rubric.score(rubric.components(**profile))  # type: ignore[arg-type]
            assert 0.0 <= value <= 1.0

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0.0, RiskCategory.LOW),
            (0.399, RiskCategory.LOW),
            (0.40, RiskCategory.MEDIUM),
            (0.70, RiskCategory.MEDIUM),
            (0.701, RiskCategory.HIGH),
            (1.0, RiskCategory.HIGH),
        ],
    )
    def test_category_boundaries(self, value: float, expected: RiskCategory) -> None:
        assert rubric.categorise(value) is expected

    def test_stated_appetite_is_not_overridden_by_capacity(self) -> None:
        """The rubric's central judgment: willingness and capacity are different
        things. A young, wealthy, self-described conservative investor should land in
        MEDIUM, not be told they are HIGH risk because they can afford to be."""
        cautious_but_capable = {**AGGRESSIVE, "risk_appetite": RiskAppetite.CONSERVATIVE}
        category = rubric.categorise(rubric.score(rubric.components(**cautious_but_capable)))
        assert category is RiskCategory.MEDIUM

    def test_zero_income_does_not_divide_by_zero(self) -> None:
        """A student, or someone between jobs — a real answer, not a bad one."""
        assert 0.0 <= rubric.savings_ratio_component(Decimal("0"), Decimal("5000")) <= 1.0
        assert rubric.savings_ratio_component(Decimal("0"), Decimal("0")) == 0.0

    def test_no_income_and_small_savings_is_not_a_full_buffer(self) -> None:
        """Regression. The first version of this rule returned 1.0 for anyone with no
        income and any savings at all, reasoning that their savings were infinite
        relative to their income. The consequence was that a 21-year-old with 2,000
        saved and nothing coming in scored the *maximum* buffer and came out HIGH
        risk capacity — the most precarious case in the input space, rated safest."""
        precarious = rubric.savings_ratio_component(Decimal("0"), Decimal("2000"))
        comfortable = rubric.savings_ratio_component(Decimal("0"), Decimal("450000"))

        assert precarious < 0.1, "a tiny buffer must not score as a large one"
        assert comfortable > precarious

    def test_a_penniless_student_is_not_classified_high_risk(self) -> None:
        """The end-to-end version of the regression above, at the level that
        actually reaches a user."""
        student = {
            "age": 21,
            "income": Decimal("0"),
            "savings": Decimal("2000"),
            "risk_appetite": RiskAppetite.MODERATE,
            "investment_horizon": 30,
            "experience": InvestmentExperience.BEGINNER,
            "financial_literacy": FinancialLiteracy.MEDIUM,
        }
        category = rubric.categorise(rubric.score(rubric.components(**student)))  # type: ignore[arg-type]
        assert category is not RiskCategory.HIGH

    def test_components_saturate_rather_than_growing_without_bound(self) -> None:
        assert rubric.horizon_component(25) == rubric.horizon_component(60) == 1.0
        assert rubric.age_component(75) == rubric.age_component(99) == 0.0
        assert rubric.savings_ratio_component(Decimal("100"), Decimal("300")) == pytest.approx(1.0)
        assert rubric.savings_ratio_component(Decimal("100"), Decimal("9000")) == pytest.approx(1.0)

    def test_more_of_a_good_thing_never_lowers_the_score(self) -> None:
        base = rubric.score(rubric.components(**CONSERVATIVE))
        longer = rubric.score(rubric.components(**{**CONSERVATIVE, "investment_horizon": 20}))
        assert longer >= base


class TestDataset:
    def test_sampling_is_reproducible(self) -> None:
        assert dataset.sample_population(50, seed=5) == dataset.sample_population(50, seed=5)

    def test_a_different_seed_gives_a_different_population(self) -> None:
        assert dataset.sample_population(50, seed=5) != dataset.sample_population(50, seed=6)

    def test_encoding_produces_the_declared_columns_in_order(self) -> None:
        frame = dataset.encode_profile(**CONSERVATIVE)  # type: ignore[arg-type]
        assert tuple(frame.columns) == dataset.FEATURE_COLUMNS
        assert len(frame) == 1

    def test_every_class_is_represented(self) -> None:
        """A class the population never produces is one the model can never predict."""
        data = dataset.build_dataset(2000, seed=3)
        distribution = dataset.category_distribution(data.labels)
        assert all(count > 0 for count in distribution.values()), distribution

    def test_no_raw_income_reaches_the_feature_matrix(self) -> None:
        """Log1p-transformed, so an artifact or a debug dump cannot leak a salary."""
        frame = dataset.encode_profile(**CONSERVATIVE)  # type: ignore[arg-type]
        assert frame["income"].iloc[0] != float(CONSERVATIVE["income"])  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def trained() -> tuple[risk_model.RiskArtifact, dict]:
    """Trained once for the module — the fit is the slow part, not the assertions."""
    return risk_model.train(population=2000)


class TestModel:
    def test_it_reproduces_the_rubric_well(self, trained) -> None:
        """A high score is expected, not impressive: the model is fitting a
        deterministic function we wrote. See rubric.py."""
        _, metrics = trained
        assert metrics["accuracy"] > 0.85
        assert metrics["f1_macro"] > 0.80

    def test_it_beats_the_majority_class_baseline(self, trained) -> None:
        _, metrics = trained
        assert metrics["beats_baseline"] is True
        assert metrics["f1_macro"] > metrics["baseline_f1_macro"]

    def test_metrics_carry_their_own_caveat(self, trained) -> None:
        """§18's reported-metrics discipline: the number must not travel without the
        context that it measures fidelity to a rubric."""
        _, metrics = trained
        assert "rubric" in metrics["label_source"]
        assert "not correctness about real people" in metrics["label_source"]

    def test_classification_is_deterministic(self, trained) -> None:
        """FR-03 acceptance criterion 2, verbatim: the same profile twice must give
        the same category and score."""
        artifact, _ = trained
        first = risk_model.classify(artifact, **CONSERVATIVE)  # type: ignore[arg-type]
        second = risk_model.classify(artifact, **CONSERVATIVE)  # type: ignore[arg-type]

        assert first.category is second.category
        assert first.score == second.score
        assert first.top_factors == second.top_factors

    def test_it_returns_exactly_three_factors(self, trained) -> None:
        """FR-03 acceptance criterion 1: category, numeric score, top-3 factors."""
        artifact, _ = trained
        result = risk_model.classify(artifact, **AGGRESSIVE)  # type: ignore[arg-type]

        assert len(result.top_factors) == 3
        assert result.category in set(RiskCategory)
        assert 0.0 <= result.score <= 1.0

    def test_factors_are_ranked_and_human_readable(self, trained) -> None:
        artifact, _ = trained
        factors = risk_model.classify(artifact, **AGGRESSIVE).top_factors  # type: ignore[arg-type]

        contributions = [factor["contribution"] for factor in factors]
        assert contributions == sorted(contributions, reverse=True)
        for factor in factors:
            assert factor["detail"].startswith("Your ")
            assert factor["factor"] in rubric.FACTOR_LABELS.values()

    def test_factors_differ_between_different_profiles(self, trained) -> None:
        """Per-user contributions, not the model's global importances — otherwise
        every user gets the same three reasons."""
        artifact, _ = trained
        cautious = risk_model.classify(artifact, **CONSERVATIVE)  # type: ignore[arg-type]
        bold = risk_model.classify(artifact, **AGGRESSIVE)  # type: ignore[arg-type]

        assert [f["contribution"] for f in cautious.top_factors] != [
            f["contribution"] for f in bold.top_factors
        ]

    def test_the_artifact_carries_no_training_rows(self, trained) -> None:
        """§11.2 — an artifact is a file that gets copied around far more casually
        than a database. It must hold fitted parameters and nothing else."""
        artifact, _ = trained
        assert not hasattr(artifact.classifier, "X_")
        assert _field_names(artifact) == {
            "classifier",
            "feature_columns",
            "feature_importances",
        }

    def test_it_classifies_the_extremes_correctly(self, trained) -> None:
        artifact, _ = trained
        assert risk_model.classify(artifact, **CONSERVATIVE).category is RiskCategory.LOW  # type: ignore[arg-type]
        assert risk_model.classify(artifact, **AGGRESSIVE).category is RiskCategory.HIGH  # type: ignore[arg-type]

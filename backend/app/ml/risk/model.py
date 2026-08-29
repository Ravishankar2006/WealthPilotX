"""Risk classification model (FR-03).

A Random Forest trained on the rubric-labelled synthetic population. See
`rubric.py` for why that is the arrangement and what it means for the metrics.

Determinism is a requirement, not a nicety: FR-03's second acceptance criterion
says the same profile submitted twice must give the same category and score. So the
seed is fixed, the forest is deterministic at inference, and — importantly — the
served *score* is the rubric's own continuous score rather than a class probability.
A probability would move with every retrain, and "your risk score changed" is a
thing a user would reasonably ask about.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.inspection import permutation_importance
from sklearn.model_selection import train_test_split

from app.ml import evaluation
from app.ml.risk import dataset, rubric
from app.models.enums import (
    FinancialLiteracy,
    InvestmentExperience,
    RiskAppetite,
    RiskCategory,
)

RANDOM_SEED = 20260301
TEST_FRACTION = 0.2
TOP_FACTOR_COUNT = 3

# Modest depth on purpose. The target is a smooth weighted sum with three
# thresholds; an unbounded forest would memorise it exactly and report a
# meaningless 100%, while a shallow one has to approximate the boundaries and
# produces feature importances that reflect the rubric's actual structure.
FOREST_PARAMS: dict[str, Any] = {
    "n_estimators": 300,
    "max_depth": 12,
    "min_samples_leaf": 5,
    "random_state": RANDOM_SEED,
    "n_jobs": -1,
}


# Which model features stand in for which rubric factor, for the M6 alignment check
# below. `income`, `savings` and their ratio all serve the rubric's single
# savings-ratio factor, so they are compared as one group — the forest is free to
# split the work between them, and counting them separately would understate the
# family every time it did.
RUBRIC_FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "appetite": ("risk_appetite",),
    "horizon": ("investment_horizon",),
    "age": ("age",),
    "savings_ratio": ("income", "savings", "savings_to_income"),
    "experience": ("experience",),
    "literacy": ("financial_literacy",),
}

# Repeats for the permutation importance. Five is enough to stabilise the ranking
# on a few thousand rows without making `train-risk` noticeably slower.
PERMUTATION_REPEATS = 5


@dataclass(frozen=True, slots=True)
class RiskArtifact:
    """What gets persisted. Fitted parameters only — never training rows (§11.2)."""

    classifier: RandomForestClassifier
    feature_columns: tuple[str, ...]
    feature_importances: dict[str, float]


@dataclass(frozen=True, slots=True)
class RiskResult:
    category: RiskCategory
    score: float
    top_factors: list[dict[str, Any]]


def train(
    *, population: int = dataset.DEFAULT_POPULATION, seed: int = dataset.DEFAULT_SEED
) -> tuple[RiskArtifact, dict[str, Any]]:
    """Train and evaluate. Returns the artifact and its §18 metrics."""
    data = dataset.build_dataset(population, seed)

    x_train, x_test, y_train, y_test = train_test_split(
        data.features,
        data.labels,
        test_size=TEST_FRACTION,
        random_state=RANDOM_SEED,
        # The rubric's thresholds give unequal classes; stratifying keeps the rare
        # class present in the test set instead of leaving it to chance.
        stratify=data.labels,
    )

    classifier = RandomForestClassifier(**FOREST_PARAMS)
    classifier.fit(x_train, y_train)

    predictions = classifier.predict(x_test)
    metrics: dict[str, Any] = dict(evaluation.classification_metrics(y_test, predictions))

    baseline = evaluation.majority_class_prediction(y_train, len(y_test))
    baseline_metrics = evaluation.classification_metrics(y_test, baseline)

    metrics["baseline_f1_macro"] = baseline_metrics["f1_macro"]
    metrics["beats_baseline"] = metrics["f1_macro"] > baseline_metrics["f1_macro"]
    metrics["class_distribution"] = dataset.category_distribution(data.labels)
    metrics["population"] = population
    # Carried in the metrics so it travels with the model wherever the row is read.
    metrics["label_source"] = (
        "Synthetic population labelled by app/ml/risk/rubric.py. Metrics measure "
        "fidelity to that rubric, not correctness about real people."
    )

    importances = {
        column: float(value)
        for column, value in zip(
            data.features.columns, classifier.feature_importances_, strict=True
        )
    }

    artifact = RiskArtifact(
        classifier=classifier,
        feature_columns=tuple(data.features.columns),
        feature_importances=importances,
    )

    # Computed at training time rather than on demand so it travels in the registry
    # row with the metrics it belongs beside. A validation result that has to be
    # re-run to be read is one nobody reads.
    metrics["rubric_alignment"] = rubric_alignment(artifact, x_test, y_test)

    return artifact, metrics


def rubric_alignment(
    artifact: "RiskArtifact",
    x_test: pd.DataFrame,
    y_test: pd.Series,
    *,
    seed: int = RANDOM_SEED,
) -> dict[str, Any]:
    """Does the forest rely on the factors the rubric says matter? (M6, §10.5.)

    This is model *validation*, not user-facing explanation. The rubric is a rule
    this repo wrote, so its per-factor contributions are already exact and are what
    FR-03 serves. The open question is whether the forest fitted to those labels
    learned the rule or learned a shortcut through it — a forest that reproduces the
    labels while keying almost entirely on `age` would score well on F1 and be wrong
    for reasons nobody had looked at.

    Permutation importance rather than the stored impurity importances: impurity
    importance is biased toward high-cardinality continuous features, and half the
    inputs here are three-level ordinals, so the comparison would be rigged against
    exactly the factors carrying the most weight.

    **The shares are not expected to equal the weights, and a mismatch is not
    automatically a fault.** Permutation importance measures how much the *fitted
    model* degrades when a column is shuffled, which depends on that column's spread
    in the population as well as its coefficient. A heavily weighted factor with
    little variation across users can matter enormously to the rule and barely at
    all to the model's error. So this reports the ordering agreement and both
    tables, and leaves the judgement to a reader of the model card.
    """
    result = permutation_importance(
        artifact.classifier,
        x_test,
        y_test,
        n_repeats=PERMUTATION_REPEATS,
        random_state=seed,
        n_jobs=-1,
    )

    per_feature = {
        column: float(value)
        for column, value in zip(x_test.columns, result.importances_mean, strict=True)
    }

    grouped = {
        factor: sum(max(per_feature.get(column, 0.0), 0.0) for column in columns)
        for factor, columns in RUBRIC_FEATURE_GROUPS.items()
    }
    total = sum(grouped.values())
    # A total of zero means shuffling every column changed nothing, which would mean
    # the forest ignores its inputs entirely. Guarding rather than dividing by zero,
    # because that state should surface as null shares in the model card, not as a
    # traceback halfway through training.
    shares = (
        {factor: round(value / total, 4) for factor, value in grouped.items()}
        if total > 0
        else dict.fromkeys(grouped, 0.0)
    )

    declared = rubric.declared_weights()
    by_share = sorted(shares, key=lambda k: shares[k], reverse=True)
    by_weight = sorted(declared, key=lambda k: declared[k], reverse=True)

    return {
        "method": f"permutation_importance, {PERMUTATION_REPEATS} repeats, held-out test set",
        "permutation_importance": {k: round(v, 6) for k, v in per_feature.items()},
        "importance_share": shares,
        "declared_weight": declared,
        "ranking_by_share": by_share,
        "ranking_by_weight": by_weight,
        # Set comparison, not sequence: the rubric's 0.10 pair are tied by design, so
        # their relative order carries no information to agree or disagree with.
        "top3_agree": set(by_share[:3]) == set(by_weight[:3]),
        "largest_share_divergence": round(max(abs(shares[k] - declared[k]) for k in declared), 4),
    }


def _top_factors(parts: rubric.RubricComponents) -> list[dict[str, Any]]:
    """FR-03's "main factors influencing the classification".

    Per-user weighted contributions, not the model's global feature importances.
    Importances are identical for every user and would answer "what does this model
    generally rely on?" — a different question from "why was *I* classified this
    way?", which is the one the user is asking.
    """
    contributions = rubric.weighted_contributions(parts)
    ranked = sorted(contributions.items(), key=lambda item: item[1], reverse=True)

    return [
        {
            "factor": rubric.FACTOR_LABELS[key],
            "contribution": round(value, 4),
            "detail": _factor_detail(key, parts),
        }
        for key, value in ranked[:TOP_FACTOR_COUNT]
    ]


def _factor_detail(key: str, parts: rubric.RubricComponents) -> str:
    """A plain-language note per factor. FR-13's baseline requirement is a
    rule-derived reason string, and this is the risk surface's version of it."""
    value = parts.as_dict()[key]
    strength = (
        "strongly increases"
        if value > 0.66
        else "moderately increases"
        if value > 0.33
        else "limits"
    )
    return f"Your {rubric.FACTOR_LABELS[key]} {strength} the assessed capacity for risk."


def classify(
    artifact: RiskArtifact,
    *,
    age: int,
    income: Decimal,
    savings: Decimal,
    risk_appetite: RiskAppetite,
    investment_horizon: int,
    experience: InvestmentExperience,
    financial_literacy: FinancialLiteracy,
) -> RiskResult:
    """Classify one profile.

    The category comes from the model; the score comes from the rubric. That split
    is deliberate — see the module docstring. When the two disagree the model's
    category wins, because it is what FR-03 says is being served, and the
    disagreement rate is exactly what the reported metrics measure.
    """
    profile = {
        "age": age,
        "income": income,
        "savings": savings,
        "risk_appetite": risk_appetite,
        "investment_horizon": investment_horizon,
        "experience": experience,
        "financial_literacy": financial_literacy,
    }

    frame = dataset.encode_profile(**profile)  # type: ignore[arg-type]
    frame = frame[list(artifact.feature_columns)]

    predicted = str(artifact.classifier.predict(frame)[0])

    parts = rubric.components(**profile)  # type: ignore[arg-type]
    return RiskResult(
        category=RiskCategory(predicted),
        score=round(rubric.score(parts), 5),
        top_factors=_top_factors(parts),
    )


def predict_frame(artifact: RiskArtifact, features: pd.DataFrame) -> np.ndarray:
    """Batch prediction, used by the fixed-evaluation-set regression test (§20)."""
    return artifact.classifier.predict(features[list(artifact.feature_columns)])

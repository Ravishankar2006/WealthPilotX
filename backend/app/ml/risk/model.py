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
    return artifact, metrics


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

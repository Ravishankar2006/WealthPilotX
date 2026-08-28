"""Synthetic population for training the risk classifier (Phase 3 plan, decision 1).

Samples profiles from plausible marginals, labels them with `rubric.py`, and returns
a training matrix. Seeded throughout: the same seed gives the same population, which
is what makes a model's reported metrics reproducible rather than anecdotal.

**Fields are sampled independently**, so the population contains combinations that
are rare in reality — a 22-year-old with a 40-year horizon and an advanced-experience
conservative appetite, say. That is deliberate. The API accepts any combination the
FR-02 validators allow, so the classifier has to behave sensibly across that whole
space, not only the populated corner of it. Sampling from a "realistic" joint
distribution would leave the model extrapolating exactly where a real user is most
likely to be unusual.
"""

from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import pandas as pd

from app.ml.risk import rubric
from app.models.enums import (
    FinancialLiteracy,
    InvestmentExperience,
    RiskAppetite,
    RiskCategory,
)

DEFAULT_POPULATION = 20_000
DEFAULT_SEED = 20260301

# Column order is part of the model contract — see the note in features/market.py.
FEATURE_COLUMNS: tuple[str, ...] = (
    "age",
    "income",
    "savings",
    "savings_to_income",
    "investment_horizon",
    "risk_appetite",
    "experience",
    "financial_literacy",
)

TARGET_COLUMN = "risk_category"

# Ordinal encodings. Ordered, not one-hot: these variables have a real ordering, and
# a tree can express "experience >= INTERMEDIATE" in one split rather than three.
APPETITE_ENCODING = {
    RiskAppetite.CONSERVATIVE: 0,
    RiskAppetite.MODERATE: 1,
    RiskAppetite.AGGRESSIVE: 2,
}
EXPERIENCE_ENCODING = {
    InvestmentExperience.NONE: 0,
    InvestmentExperience.BEGINNER: 1,
    InvestmentExperience.INTERMEDIATE: 2,
    InvestmentExperience.ADVANCED: 3,
}
LITERACY_ENCODING = {
    FinancialLiteracy.LOW: 0,
    FinancialLiteracy.MEDIUM: 1,
    FinancialLiteracy.HIGH: 2,
}


@dataclass(frozen=True, slots=True)
class RiskDataset:
    features: pd.DataFrame
    labels: pd.Series
    scores: pd.Series


def encode_profile(
    *,
    age: int,
    income: Decimal,
    savings: Decimal,
    risk_appetite: RiskAppetite,
    investment_horizon: int,
    experience: InvestmentExperience,
    financial_literacy: FinancialLiteracy,
) -> pd.DataFrame:
    """One profile as the single-row frame the model expects (FR-06 encoding).

    `savings_to_income` is supplied explicitly rather than left for the trees to
    discover: a ratio is the quantity that actually carries meaning, and a tree
    approximating a division through axis-aligned splits needs far more data to
    reach the same place.
    """
    income_float = float(income)
    savings_float = float(savings)
    ratio = savings_float / income_float if income_float > 0 else float(savings_float > 0)

    return pd.DataFrame(
        [
            {
                "age": float(age),
                # Log1p: income and savings span orders of magnitude, and the
                # untransformed scale would let a handful of very high earners
                # dominate every split threshold.
                "income": float(np.log1p(income_float)),
                "savings": float(np.log1p(savings_float)),
                "savings_to_income": min(ratio, 10.0),
                "investment_horizon": float(investment_horizon),
                "risk_appetite": float(APPETITE_ENCODING[RiskAppetite(risk_appetite)]),
                "experience": float(EXPERIENCE_ENCODING[InvestmentExperience(experience)]),
                "financial_literacy": float(
                    LITERACY_ENCODING[FinancialLiteracy(financial_literacy)]
                ),
            }
        ],
        columns=list(FEATURE_COLUMNS),
    )


def sample_population(
    size: int = DEFAULT_POPULATION, seed: int = DEFAULT_SEED
) -> list[dict[str, object]]:
    """Draw `size` profiles from plausible per-field marginals."""
    rng = np.random.default_rng(seed)

    ages = rng.integers(18, 81, size=size)
    # Lognormal: incomes are right-skewed, and a normal draw would produce negatives.
    incomes = np.clip(rng.lognormal(mean=10.9, sigma=0.6, size=size), 5_000, 2_000_000)
    # Savings as a multiple of income rather than an absolute figure, so the pairing
    # stays plausible across the whole income range.
    savings = incomes * np.clip(rng.lognormal(mean=-0.7, sigma=1.1, size=size), 0.0, 12.0)
    horizons = rng.integers(1, 41, size=size)

    appetites = rng.choice(list(RiskAppetite), size=size, p=[0.3, 0.45, 0.25])
    experiences = rng.choice(list(InvestmentExperience), size=size, p=[0.25, 0.35, 0.28, 0.12])
    literacies = rng.choice(list(FinancialLiteracy), size=size, p=[0.3, 0.45, 0.25])

    return [
        {
            "age": int(ages[i]),
            "income": Decimal(str(round(float(incomes[i]), 2))),
            "savings": Decimal(str(round(float(savings[i]), 2))),
            "investment_horizon": int(horizons[i]),
            "risk_appetite": appetites[i],
            "experience": experiences[i],
            "financial_literacy": literacies[i],
        }
        for i in range(size)
    ]


def build_dataset(size: int = DEFAULT_POPULATION, seed: int = DEFAULT_SEED) -> RiskDataset:
    """Sample, label with the rubric, and encode."""
    population = sample_population(size, seed)

    rows: list[pd.DataFrame] = []
    labels: list[str] = []
    scores: list[float] = []

    for profile in population:
        parts = rubric.components(**profile)  # type: ignore[arg-type]
        value = rubric.score(parts)
        rows.append(encode_profile(**profile))  # type: ignore[arg-type]
        labels.append(str(rubric.categorise(value)))
        scores.append(value)

    features = pd.concat(rows, ignore_index=True)
    return RiskDataset(
        features=features,
        labels=pd.Series(labels, name=TARGET_COLUMN),
        scores=pd.Series(scores, name="risk_score"),
    )


def category_distribution(labels: pd.Series) -> dict[str, int]:
    """Class balance, reported alongside the metrics.

    Worth publishing: the rubric's thresholds do not produce three equal classes, and
    an accuracy figure means something different against a skewed base rate.
    """
    counts = labels.value_counts().to_dict()
    return {str(category): int(counts.get(str(category), 0)) for category in RiskCategory}

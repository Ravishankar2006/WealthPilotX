"""Per-asset suitability scoring (FR-10, PRD §10.3).

§10.3 describes "User Profile + Risk Class + Asset Features → KNN / Recommendation
Engine → Candidate Assets". This is the scoring half; `candidates.py` is the KNN
selection half.

**Why this is content-based rather than collaborative** (Phase 4 plan, judgment call
1): collaborative filtering needs user–item interaction history, and there is none —
no user has ever held or rated an asset. Manufacturing implicit feedback from
previously generated portfolios would be a recommender learning from its own output,
which converges on its own prior rather than on anything true.

So assets are scored on their own measurable properties against a target profile
derived from the user's risk class. The score orders the candidate set; the optimizer
sets the weights. FR-10's acceptance criterion is explicit that the weights must come
from the optimizer and not from a lookup table, so this module deliberately stops at
ordering.
"""

from dataclasses import dataclass

import numpy as np

from app.models.enums import AssetClass, InvestmentGoal, RiskCategory

# The volatility each risk class is aiming at, annualised. Assets are scored on
# distance from this rather than on "less volatility is better" — a LOW-risk user is
# not best served by the single least volatile asset, and a HIGH-risk one is not best
# served by the most volatile.
TARGET_VOLATILITY: dict[RiskCategory, float] = {
    RiskCategory.LOW: 0.08,
    RiskCategory.MEDIUM: 0.14,
    RiskCategory.HIGH: 0.22,
}

# Component weights in the suitability score. Return expectation matters, but not so
# much that it overwhelms whether the asset suits the user at all.
WEIGHT_RETURN = 0.35
WEIGHT_VOLATILITY_FIT = 0.30
WEIGHT_MOMENTUM = 0.15
WEIGHT_CONFIDENCE = 0.10
WEIGHT_GOAL_FIT = 0.10

assert (
    abs(
        WEIGHT_RETURN
        + WEIGHT_VOLATILITY_FIT
        + WEIGHT_MOMENTUM
        + WEIGHT_CONFIDENCE
        + WEIGHT_GOAL_FIT
        - 1.0
    )
    < 1e-9
), "Scoring weights must sum to 1.0"

# How well each asset class serves each goal. Not a lookup table of *allocations* —
# FR-10 forbids those — but a statement about suitability that feeds one component of
# an ordering.
GOAL_CLASS_FIT: dict[InvestmentGoal, dict[AssetClass, float]] = {
    InvestmentGoal.RETIREMENT: {
        AssetClass.BOND: 1.0,
        AssetClass.EQUITY: 0.5,
        AssetClass.REAL_ESTATE: 0.7,
        AssetClass.COMMODITY: 0.3,
        AssetClass.CASH: 0.8,
    },
    InvestmentGoal.GROWTH: {
        AssetClass.EQUITY: 1.0,
        AssetClass.REAL_ESTATE: 0.6,
        AssetClass.COMMODITY: 0.5,
        AssetClass.BOND: 0.3,
        AssetClass.CASH: 0.1,
    },
    InvestmentGoal.WEALTH_CREATION: {
        AssetClass.EQUITY: 0.9,
        AssetClass.REAL_ESTATE: 0.7,
        AssetClass.COMMODITY: 0.6,
        AssetClass.BOND: 0.5,
        AssetClass.CASH: 0.3,
    },
}


@dataclass(frozen=True, slots=True)
class AssetFeatures:
    symbol: str
    asset_class: AssetClass
    expected_return: float
    volatility: float
    momentum: float
    confidence: float


@dataclass(frozen=True, slots=True)
class ScoredAsset:
    symbol: str
    score: float
    # Per-component contributions, kept so `reasons.py` can name what actually drove
    # the score rather than inventing a plausible-sounding justification.
    components: dict[str, float]
    features: AssetFeatures


def _normalise(values: list[float]) -> list[float]:
    """Min-max to [0, 1] across the candidate set.

    Relative, not absolute: "the best available expected return today" is the
    meaningful comparison when ranking a fixed universe, and an absolute scale would
    make every asset score low in a flat market.
    """
    if not values:
        return []
    low, high = min(values), max(values)
    if high - low < 1e-12:
        return [0.5] * len(values)
    return [(value - low) / (high - low) for value in values]


def _volatility_fit(volatility: float, target: float) -> float:
    """1.0 at the target, decaying either side."""
    if target <= 0:
        return 0.0
    return float(np.exp(-abs(volatility - target) / target))


def score_assets(
    assets: list[AssetFeatures],
    *,
    risk_category: RiskCategory,
    goal: InvestmentGoal,
) -> list[ScoredAsset]:
    """Score and rank assets for one user. Highest score first."""
    if not assets:
        return []

    target = TARGET_VOLATILITY[risk_category]
    goal_fit = GOAL_CLASS_FIT[goal]

    returns = _normalise([a.expected_return for a in assets])
    momenta = _normalise([a.momentum for a in assets])

    scored: list[ScoredAsset] = []
    for asset, return_score, momentum_score in zip(assets, returns, momenta, strict=True):
        components = {
            "expected_return": WEIGHT_RETURN * return_score,
            "volatility_fit": WEIGHT_VOLATILITY_FIT * _volatility_fit(asset.volatility, target),
            "momentum": WEIGHT_MOMENTUM * momentum_score,
            "prediction_confidence": WEIGHT_CONFIDENCE * max(0.0, min(1.0, asset.confidence)),
            "goal_fit": WEIGHT_GOAL_FIT * goal_fit.get(asset.asset_class, 0.5),
        }
        scored.append(
            ScoredAsset(
                symbol=asset.symbol,
                score=round(sum(components.values()), 6),
                components={k: round(v, 6) for k, v in components.items()},
                features=asset,
            )
        )

    return sorted(scored, key=lambda item: (-item.score, item.symbol))

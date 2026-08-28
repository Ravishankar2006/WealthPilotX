"""Recommendation metrics (PRD §18: Precision@K, Recall@K, NDCG).

**Read this before quoting any number from here.**

There is no ground truth about which assets a user "should" have been shown. Nobody
has been advised, followed the advice, and had an outcome recorded. So relevance is
*rule-derived*, exactly as M3's risk labels are, and the same caveat applies: these
metrics measure agreement between the ranker and a second rule we wrote, not
correctness about real investors.

The one thing done to make them slightly more than tautological: the relevance rule
is deliberately **not** the scoring function. Relevance is a coarse suitability
test — is this asset's volatility inside the band for this risk class, and is its
class permitted meaningful weight for this goal? The ranker is a weighted
five-component score. They can disagree, and where they do is informative about the
ranker. Where they agree, that is two of our own rules agreeing.

A high score here is therefore evidence of internal consistency, not of quality.
"""

from dataclasses import dataclass

import numpy as np

from app.ml.recommendation.scoring import (
    GOAL_CLASS_FIT,
    TARGET_VOLATILITY,
    AssetFeatures,
    ScoredAsset,
)
from app.models.enums import InvestmentGoal, RiskCategory

DEFAULT_K = 10

# An asset is relevant if its volatility is within this multiple of the risk class's
# target, either side. Wide enough that relevance is a band rather than a point.
VOLATILITY_TOLERANCE = 0.6

# And if its class is a reasonable fit for the goal.
MIN_GOAL_FIT = 0.5


@dataclass(frozen=True, slots=True)
class RankingMetrics:
    precision_at_k: float
    recall_at_k: float
    ndcg_at_k: float
    k: int
    relevant_total: int

    def as_dict(self) -> dict[str, float | int]:
        return {
            "precision_at_k": round(self.precision_at_k, 6),
            "recall_at_k": round(self.recall_at_k, 6),
            "ndcg_at_k": round(self.ndcg_at_k, 6),
            "k": self.k,
            "relevant_total": self.relevant_total,
        }


def is_relevant(asset: AssetFeatures, *, risk_category: RiskCategory, goal: InvestmentGoal) -> bool:
    """The rule-derived relevance test. Coarse, and deliberately not the scorer."""
    target = TARGET_VOLATILITY[risk_category]
    within_band = abs(asset.volatility - target) <= target * VOLATILITY_TOLERANCE
    suits_goal = GOAL_CLASS_FIT[goal].get(asset.asset_class, 0.0) >= MIN_GOAL_FIT
    return within_band and suits_goal


def evaluate_ranking(
    ranked: list[ScoredAsset],
    *,
    risk_category: RiskCategory,
    goal: InvestmentGoal,
    k: int = DEFAULT_K,
) -> RankingMetrics:
    """§18's three metrics for one ranked list."""
    if not ranked:
        return RankingMetrics(0.0, 0.0, 0.0, k, 0)

    relevance = [
        1.0 if is_relevant(item.features, risk_category=risk_category, goal=goal) else 0.0
        for item in ranked
    ]
    total_relevant = int(sum(relevance))
    top = relevance[:k]

    precision = float(sum(top) / len(top)) if top else 0.0
    recall = float(sum(top) / total_relevant) if total_relevant else 0.0

    return RankingMetrics(
        precision_at_k=precision,
        recall_at_k=recall,
        ndcg_at_k=_ndcg(top, relevance, k),
        k=k,
        relevant_total=total_relevant,
    )


def _ndcg(top: list[float], relevance: list[float], k: int) -> float:
    """Normalised discounted cumulative gain.

    DCG rewards putting relevant items early; the ideal DCG is what a perfect
    ranking of the same relevance labels would score. The ratio is what makes the
    number comparable across users with different numbers of relevant assets.
    """
    if not top:
        return 0.0

    discounts = 1.0 / np.log2(np.arange(2, len(top) + 2))
    dcg = float(np.sum(np.asarray(top) * discounts))

    ideal_labels = sorted(relevance, reverse=True)[:k]
    ideal_discounts = 1.0 / np.log2(np.arange(2, len(ideal_labels) + 2))
    idcg = float(np.sum(np.asarray(ideal_labels) * ideal_discounts))

    return dcg / idcg if idcg > 0 else 0.0

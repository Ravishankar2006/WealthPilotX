"""KNN candidate selection (PRD §10.3).

§10.3 names KNN in the recommendation path. The honest reading, given there is no
user–item interaction history to do collaborative filtering with, is KNN over the
*asset feature space*: place a target point representing what this user's risk class
is looking for, and take the nearest assets to it.

That does real work rather than being a nod to the spec. Pure score-ranking would
happily return the six highest-scoring assets when all six are large-cap equities;
nearest-neighbour selection in a standardised feature space, combined with the
per-class quotas below, returns a candidate set the optimizer can actually
diversify across. The optimizer still sets the weights (FR-10's acceptance
criterion) — this only decides which assets it gets to choose from.
"""

from dataclasses import dataclass

import numpy as np
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

from app.ml.recommendation.scoring import TARGET_VOLATILITY, ScoredAsset
from app.models.enums import AssetClass, RiskCategory

# How many assets reach the optimizer. Enough that diversification is possible,
# small enough that every holding can be explained (FR-13) and the covariance
# estimate stays well-conditioned against the available history.
DEFAULT_CANDIDATE_COUNT = 12

# At least this many candidates per represented asset class, so the per-class floors
# in the constraint set are satisfiable. Without it, a HIGH-risk user could get
# twelve equities and an infeasible 5% bond floor.
MIN_PER_CLASS = 2

# The return the target point assumes. Deliberately modest: a target that asks for
# the highest available return would collapse KNN back into score-ranking.
TARGET_RETURN_QUANTILE = 0.6


@dataclass(frozen=True, slots=True)
class CandidateSet:
    assets: tuple[ScoredAsset, ...]
    target: dict[str, float]

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(asset.symbol for asset in self.assets)


def _feature_matrix(assets: list[ScoredAsset]) -> np.ndarray:
    return np.array(
        [[a.features.expected_return, a.features.volatility, a.features.momentum] for a in assets],
        dtype=float,
    )


def select_candidates(
    scored: list[ScoredAsset],
    *,
    risk_category: RiskCategory,
    count: int = DEFAULT_CANDIDATE_COUNT,
    min_per_class: int = MIN_PER_CLASS,
    required_per_class: dict[AssetClass, int] | None = None,
) -> CandidateSet:
    """Nearest neighbours to the user's target profile, with per-class quotas.

    `required_per_class` comes from the constraint floors the optimizer will apply
    (`constraints.required_asset_counts`). Honouring it here is what stops the
    selector handing the optimizer a set that cannot satisfy its own constraints —
    a 45% bond floor against two bonds capped at 20% each has no solution, and the
    user sees a 422 for a request that was perfectly reasonable.
    """
    if not scored:
        return CandidateSet(assets=(), target={})

    matrix = _feature_matrix(scored)
    # Standardised before distances are taken: an annualised return of 0.08 and a
    # volatility of 0.20 are not comparable magnitudes, and an unscaled Euclidean
    # distance would be dominated by whichever feature happens to have the wider
    # spread.
    scaler = StandardScaler().fit(matrix)

    returns = matrix[:, 0]
    target_raw = np.array(
        [
            float(np.quantile(returns, TARGET_RETURN_QUANTILE)),
            TARGET_VOLATILITY[risk_category],
            # Neutral on momentum: the target is a risk/return profile, and letting
            # it chase recent winners would make the candidate set trend-following.
            float(np.median(matrix[:, 2])),
        ]
    )
    target = scaler.transform(target_raw.reshape(1, -1))

    neighbours = NearestNeighbors(n_neighbors=min(len(scored), max(count, 1)))
    neighbours.fit(scaler.transform(matrix))
    _, indices = neighbours.kneighbors(target)

    chosen: list[ScoredAsset] = [scored[int(i)] for i in indices[0]]
    chosen = _ensure_class_coverage(
        chosen,
        scored,
        min_per_class=min_per_class,
        count=count,
        required_per_class=required_per_class or {},
    )

    return CandidateSet(
        assets=tuple(sorted(chosen, key=lambda a: (-a.score, a.symbol))),
        target={
            "expected_return": round(float(target_raw[0]), 6),
            "volatility": round(float(target_raw[1]), 6),
            "momentum": round(float(target_raw[2]), 6),
        },
    )


def _ensure_class_coverage(
    chosen: list[ScoredAsset],
    universe: list[ScoredAsset],
    *,
    min_per_class: int,
    count: int,
    required_per_class: dict[AssetClass, int],
) -> list[ScoredAsset]:
    """Top up under-represented classes so the per-class floors stay satisfiable.

    Nearest-neighbour selection has no notion of asset class, so it can return a
    candidate set that makes the constraint set infeasible — twelve equities against
    a 5% bond floor. Topping up here means the failure never reaches the optimizer.
    """
    by_class: dict[AssetClass, list[ScoredAsset]] = {}
    for asset in universe:
        by_class.setdefault(asset.features.asset_class, []).append(asset)

    selected = {asset.symbol: asset for asset in chosen}

    def quota_for(cls: AssetClass, available: int) -> int:
        # The larger of the general diversification floor and whatever the
        # constraint set will actually require of this class.
        return min(max(min_per_class, required_per_class.get(cls, 0)), available)

    for cls, available in by_class.items():
        present = sum(1 for a in selected.values() if a.features.asset_class is cls)
        needed = quota_for(cls, len(available)) - present
        if needed <= 0:
            continue
        for candidate in sorted(available, key=lambda a: (-a.score, a.symbol)):
            if needed <= 0:
                break
            if candidate.symbol not in selected:
                selected[candidate.symbol] = candidate
                needed -= 1

    # Topping up can overshoot the requested count. Trim the lowest-scoring assets,
    # but never below the per-class minimum that was just established.
    result = sorted(selected.values(), key=lambda a: (-a.score, a.symbol))
    if len(result) <= count:
        return result

    kept: list[ScoredAsset] = []
    counts: dict[AssetClass, int] = {}
    for asset in result:
        cls = asset.features.asset_class
        quota = quota_for(cls, len(by_class[cls]))
        if len(kept) < count or counts.get(cls, 0) < quota:
            kept.append(asset)
            counts[cls] = counts.get(cls, 0) + 1
    return kept

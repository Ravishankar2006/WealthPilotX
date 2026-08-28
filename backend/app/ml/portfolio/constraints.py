"""Allocation constraints (FR-11, Phase 4 plan decision 3).

FR-11 requires "allocation constraints such as maximum weights by asset class". This
module is where the user's risk class, goal and horizon turn into those constraints.

Encoding them as constraints rather than as extra terms in the objective is
deliberate. The objective stays one interpretable expression, and the constraint set
becomes an inspectable list of statements — "at most 35% equities, at least 40%
bonds" — which is the form a user's actual question takes. It is also the form that
can be checked for feasibility *before* solving, so an impossible request produces a
clear error instead of a silently truncated portfolio (FR-11's second criterion).
"""

import math
from dataclasses import dataclass, field
from typing import Any

from app.models.enums import AssetClass, InvestmentGoal, RiskCategory

# Risk aversion λ in the mean-variance objective. Higher means more weight on
# variance, so LOW is far more averse than HIGH. This is where the risk class does
# its real work — the alternative, one portfolio rescaled three ways, would leave
# FR-10's goal and horizon inputs doing nothing.
RISK_AVERSION: dict[RiskCategory, float] = {
    RiskCategory.LOW: 12.0,
    RiskCategory.MEDIUM: 5.0,
    RiskCategory.HIGH: 2.0,
}

# No single position may exceed this, whatever the optimizer would prefer. The last
# brake against a corner solution when μ happens to favour one asset strongly.
MAX_WEIGHT_PER_ASSET: dict[RiskCategory, float] = {
    RiskCategory.LOW: 0.20,
    RiskCategory.MEDIUM: 0.25,
    RiskCategory.HIGH: 0.35,
}

# Per-class bands: (floor, cap). A floor is what forces diversification across
# classes; a cap alone would permit a 100%-bond "low risk" portfolio that ignores
# the growth the user asked for.
CLASS_BANDS: dict[RiskCategory, dict[AssetClass, tuple[float, float]]] = {
    RiskCategory.LOW: {
        AssetClass.EQUITY: (0.10, 0.35),
        AssetClass.BOND: (0.45, 0.80),
        AssetClass.COMMODITY: (0.00, 0.10),
        AssetClass.REAL_ESTATE: (0.00, 0.10),
        AssetClass.CASH: (0.00, 0.20),
    },
    RiskCategory.MEDIUM: {
        AssetClass.EQUITY: (0.35, 0.65),
        AssetClass.BOND: (0.20, 0.50),
        AssetClass.COMMODITY: (0.00, 0.15),
        AssetClass.REAL_ESTATE: (0.00, 0.15),
        AssetClass.CASH: (0.00, 0.10),
    },
    RiskCategory.HIGH: {
        AssetClass.EQUITY: (0.60, 0.90),
        AssetClass.BOND: (0.05, 0.30),
        AssetClass.COMMODITY: (0.00, 0.20),
        AssetClass.REAL_ESTATE: (0.00, 0.20),
        AssetClass.CASH: (0.00, 0.05),
    },
}

# Goal adjustments, applied to the equity band. RETIREMENT shifts toward capital
# preservation, GROWTH the other way, WEALTH_CREATION sits between and moves nothing.
GOAL_EQUITY_SHIFT: dict[InvestmentGoal, float] = {
    InvestmentGoal.RETIREMENT: -0.10,
    InvestmentGoal.WEALTH_CREATION: 0.00,
    InvestmentGoal.GROWTH: 0.10,
}

# Below this many years, volatility stops being a fluctuation and starts being a
# realised loss, so the equity band tightens regardless of stated appetite.
SHORT_HORIZON_YEARS = 5
SHORT_HORIZON_EQUITY_SHIFT = -0.15


@dataclass(frozen=True, slots=True)
class ConstraintSet:
    """The full constraint set for one optimization, in inspectable form."""

    risk_aversion: float
    max_weight: float
    class_bands: dict[AssetClass, tuple[float, float]]
    class_of: tuple[AssetClass, ...]
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Persisted on the portfolio row, so "why 12% and not 20%?" stays
        answerable later. Recomputing from current settings would answer a
        different question."""
        return {
            "risk_aversion": self.risk_aversion,
            "max_weight_per_asset": self.max_weight,
            "class_bands": {
                str(cls): {"floor": floor, "cap": cap}
                for cls, (floor, cap) in self.class_bands.items()
            },
            "notes": self.notes,
        }


class InfeasibleConstraintsError(Exception):
    """The constraint set admits no valid portfolio.

    FR-11's second acceptance criterion: report this clearly rather than returning a
    malformed portfolio. Carries which constraint is at fault, because "infeasible"
    on its own tells an operator nothing about what to change.
    """

    def __init__(self, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


def _clamp_band(floor: float, cap: float) -> tuple[float, float]:
    floor = max(0.0, min(1.0, floor))
    cap = max(0.0, min(1.0, cap))
    return (min(floor, cap), cap)


def build_bands(
    *, risk_category: RiskCategory, goal: InvestmentGoal, horizon_years: int
) -> tuple[dict[AssetClass, tuple[float, float]], list[str]]:
    """The per-class bands for a profile, before any asset universe is known.

    Split out from `build_constraints` because candidate selection needs the bands
    *first*: it has to pick enough assets per class to satisfy the floors it will
    later be measured against. Selecting first and constraining afterwards produced
    a candidate set that could not satisfy its own constraints — a LOW-risk user
    with a 45% bond floor and two bonds capped at 20% each has no valid portfolio,
    and got a 422 for an entirely reasonable request.
    """
    bands = dict(CLASS_BANDS[risk_category])
    notes: list[str] = [
        f"Risk category {risk_category} sets a risk-aversion of "
        f"{RISK_AVERSION[risk_category]} and a {MAX_WEIGHT_PER_ASSET[risk_category]:.0%} "
        "cap on any single holding."
    ]

    shift = GOAL_EQUITY_SHIFT[goal]
    if shift:
        floor, cap = bands[AssetClass.EQUITY]
        bands[AssetClass.EQUITY] = _clamp_band(floor + shift, cap + shift)
        notes.append(
            f"Goal {goal} shifts the equity band by {shift:+.0%}, to "
            f"{bands[AssetClass.EQUITY][0]:.0%}–{bands[AssetClass.EQUITY][1]:.0%}."
        )

    if horizon_years < SHORT_HORIZON_YEARS:
        floor, cap = bands[AssetClass.EQUITY]
        bands[AssetClass.EQUITY] = _clamp_band(
            floor + SHORT_HORIZON_EQUITY_SHIFT, cap + SHORT_HORIZON_EQUITY_SHIFT
        )
        notes.append(
            f"A {horizon_years}-year horizon is under {SHORT_HORIZON_YEARS} years, so the "
            f"equity band tightens further to {bands[AssetClass.EQUITY][0]:.0%}–"
            f"{bands[AssetClass.EQUITY][1]:.0%}."
        )

    return bands, notes


def required_asset_counts(
    bands: dict[AssetClass, tuple[float, float]],
    max_weight: float,
    available: dict[AssetClass, int] | None = None,
    baseline: int = 0,
) -> dict[AssetClass, int]:
    """How many assets each class needs for the constraint set to be solvable.

    Two separate requirements, and missing the second one shipped a bug:

    1. **Meet the floor.** A 45% bond floor against a 20% per-asset cap needs three
       holdings, not two.
    2. **Fill the portfolio.** Class caps summing above 100% is not enough, because
       a class can only hold as much as its assets are individually allowed to. A
       LOW-risk mandate caps equities at 10% and allows bonds up to 80% — but with
       only three bonds at 20% each, bonds top out at 60%, and the reachable total
       is 60 + 10 + 10 + 10 = 90%. No fully invested portfolio exists, and the user
       got an "optimization failed" for a profile that is entirely ordinary.

    So capacity is topped up greedily, largest-cap class first, until the reachable
    total covers 100%.

    `available` is how many assets each class actually has. Without it the function
    would happily require a CASH holding in a universe that tracks none, and count
    on capacity that never materialises. `baseline` is the selector's per-class
    minimum, so the arithmetic here matches what selection will really do.
    """
    supply = available if available is not None else dict.fromkeys(bands, 10**6)
    classes = [cls for cls in bands if supply.get(cls, 0) > 0]

    required = dict.fromkeys(bands, 0)
    for cls in classes:
        floor, _ = bands[cls]
        needed = math.ceil(floor / max_weight - 1e-9) if floor > 0 else 0
        required[cls] = min(max(needed, baseline), supply.get(cls, 0))

    def reachable() -> float:
        return sum(min(bands[cls][1], required[cls] * max_weight) for cls in classes)

    # Descending cap: the classes allowed to hold the most are the cheapest place to
    # buy capacity, in assets-per-percent.
    order = sorted(classes, key=lambda cls: bands[cls][1], reverse=True)
    for _ in range(100):
        if reachable() >= 1.0 - 1e-9:
            break
        progressed = False
        for cls in order:
            cap = bands[cls][1]
            if required[cls] < supply.get(cls, 0) and required[cls] * max_weight < cap - 1e-9:
                required[cls] += 1
                progressed = True
                break
        if not progressed:
            # Every class is at its cap or out of assets. `check_feasible` reports
            # this with the numbers, rather than the solver failing opaquely.
            break

    return required


def build_constraints(
    *,
    risk_category: RiskCategory,
    goal: InvestmentGoal,
    horizon_years: int,
    asset_classes: list[AssetClass],
) -> ConstraintSet:
    """Turn a user's profile and an available universe into a constraint set."""
    bands, notes = build_bands(risk_category=risk_category, goal=goal, horizon_years=horizon_years)

    # Classes with no asset available cannot carry weight; their floors would make
    # the set unsatisfiable for a reason the user cannot act on.
    present = set(asset_classes)
    dropped = [cls for cls in bands if cls not in present]
    for cls in dropped:
        bands.pop(cls)
    if dropped:
        notes.append("No tracked assets in " + ", ".join(sorted(str(c) for c in dropped)) + ".")

    constraints = ConstraintSet(
        risk_aversion=RISK_AVERSION[risk_category],
        max_weight=MAX_WEIGHT_PER_ASSET[risk_category],
        class_bands=bands,
        class_of=tuple(asset_classes),
        notes=notes,
    )
    check_feasible(constraints)
    return constraints


def check_feasible(constraints: ConstraintSet) -> None:
    """Reject an impossible constraint set before the solver sees it.

    A solver handed an infeasible problem returns *something* — a non-converged
    vector that looks like a portfolio. FR-11 requires a clear error instead, and
    catching it here means the error can say which constraint is the problem.
    """
    floors = sum(floor for floor, _ in constraints.class_bands.values())
    # Effective caps, not nominal ones: a class can hold only as much as its own
    # assets are individually permitted to. Summing the nominal caps misses that a
    # class allowed 80% with three assets capped at 20% each tops out at 60%.
    counts: dict[AssetClass, int] = {}
    for cls in constraints.class_of:
        counts[cls] = counts.get(cls, 0) + 1

    caps = sum(
        min(cap, counts.get(cls, 0) * constraints.max_weight)
        for cls, (_, cap) in constraints.class_bands.items()
    )

    if floors > 1.0 + 1e-9:
        raise InfeasibleConstraintsError(
            f"The per-class minimum allocations sum to {floors:.0%}, which exceeds 100%. "
            "No portfolio can satisfy them all.",
            {"class_floors_sum": floors},
        )

    if caps < 1.0 - 1e-9:
        raise InfeasibleConstraintsError(
            f"The available assets can hold at most {caps:.0%} under the per-class limits "
            f"and the {constraints.max_weight:.0%} per-asset cap, so a fully invested "
            "portfolio is impossible. More assets are needed in the classes with headroom.",
            {"reachable_total": caps, "assets_per_class": {str(k): v for k, v in counts.items()}},
        )

    for cls, (floor, _) in constraints.class_bands.items():
        available = counts.get(cls, 0) * constraints.max_weight
        if floor > available + 1e-9:
            raise InfeasibleConstraintsError(
                f"{cls} requires at least {floor:.0%} but only {counts.get(cls, 0)} tracked "
                f"asset(s) are available, each capped at {constraints.max_weight:.0%} — "
                f"a maximum of {available:.0%}.",
                {"asset_class": str(cls), "floor": floor, "max_available": available},
            )

    total_capacity = len(constraints.class_of) * constraints.max_weight
    if total_capacity < 1.0 - 1e-9:
        raise InfeasibleConstraintsError(
            f"{len(constraints.class_of)} assets capped at {constraints.max_weight:.0%} each "
            f"can hold at most {total_capacity:.0%}. A fully invested portfolio needs more "
            "assets or a higher per-asset cap.",
            {"max_total_weight": total_capacity},
        )

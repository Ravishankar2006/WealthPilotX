"""Mean-variance portfolio optimization (FR-11).

    maximise   μᵀw − λ · wᵀΣw
    subject to Σw = 1,  0 ≤ wᵢ ≤ max_weight,  floor_c ≤ Σ_{i∈c} wᵢ ≤ cap_c

Solved with SLSQP. FR-11's acceptance criteria are checked on the returned solution
rather than assumed from the constraint definitions — a solver that reports success
can still land marginally outside a bound, and "weights sum to 1.0 ± 0.001" is a
promise to the caller, not a hope about the library.

The other criterion — an infeasible constraint set must produce a clear error rather
than a malformed portfolio — is handled in two places: `constraints.check_feasible`
rejects impossible sets before the solve, and a non-converged solve raises here.
Neither path ever returns a best-effort weight vector, because a best-effort
portfolio is indistinguishable from a real one once it reaches a user.
"""

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import minimize

from app.ml.portfolio.constraints import ConstraintSet
from app.models.enums import AssetClass

# FR-11's tolerance, verbatim.
WEIGHT_SUM_TOLERANCE = 1e-3

# Positions below this are rounding noise, not allocations. Dropping them keeps a
# portfolio explainable — nobody wants a reason string for a 0.04% holding — and the
# remainder is renormalised so the sum-to-1 guarantee still holds.
MIN_MEANINGFUL_WEIGHT = 0.005


class OptimizationFailedError(Exception):
    """The solver did not converge. Never downgraded to a partial result."""

    def __init__(self, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


@dataclass(frozen=True, slots=True)
class OptimizedPortfolio:
    symbols: tuple[str, ...]
    weights: np.ndarray
    expected_return: float
    expected_risk: float
    objective_value: float

    def holdings(self) -> dict[str, float]:
        """Symbol → weight, excluding the positions trimmed as noise."""
        return {
            symbol: float(weight)
            for symbol, weight in zip(self.symbols, self.weights, strict=True)
            if weight > 0
        }


def _class_indices(class_of: tuple[AssetClass, ...], target: AssetClass) -> list[int]:
    return [index for index, cls in enumerate(class_of) if cls is target]


def optimize(
    mu: np.ndarray,
    sigma: np.ndarray,
    constraints: ConstraintSet,
    symbols: tuple[str, ...],
) -> OptimizedPortfolio:
    """Solve for the weights. Raises rather than returning a doubtful answer."""
    n = len(symbols)
    if n != len(mu) or sigma.shape != (n, n):
        raise OptimizationFailedError(
            "Optimizer inputs are misaligned: μ, Σ and the symbol list must describe "
            "the same assets in the same order."
        )

    lam = constraints.risk_aversion

    def negative_objective(w: np.ndarray) -> float:
        # Minimised, so the sign is flipped from the maximisation above.
        return float(-(mu @ w) + lam * float(w @ sigma @ w))

    def gradient(w: np.ndarray) -> np.ndarray:
        return -mu + 2.0 * lam * (sigma @ w)

    scipy_constraints: list[dict[str, Any]] = [
        {"type": "eq", "fun": lambda w: float(np.sum(w) - 1.0), "jac": lambda w: np.ones_like(w)}
    ]

    for cls, (floor, cap) in constraints.class_bands.items():
        indices = _class_indices(constraints.class_of, cls)
        if not indices:
            continue
        mask = np.zeros(n)
        mask[indices] = 1.0
        if floor > 0:
            scipy_constraints.append(
                {
                    "type": "ineq",
                    "fun": lambda w, m=mask, f=floor: float(m @ w - f),
                    "jac": lambda w, m=mask: m,
                }
            )
        if cap < 1.0:
            scipy_constraints.append(
                {
                    "type": "ineq",
                    "fun": lambda w, m=mask, c=cap: float(c - m @ w),
                    "jac": lambda w, m=mask: -m,
                }
            )

    bounds = [(0.0, constraints.max_weight)] * n

    # SLSQP is a local method and needs a feasible starting point. An equal-weight
    # start is not one: a LOW-risk mandate caps equities at 10% and floors bonds at
    # 45%, and 1/n violates both the moment the candidate set is equity-heavy. The
    # solver then failed with "Positive directional derivative for linesearch" and
    # the user got a 422 for a request that has a perfectly good answer.
    start = _feasible_start(constraints, n)

    result = minimize(
        negative_objective,
        start,
        jac=gradient,
        method="SLSQP",
        bounds=bounds,
        constraints=scipy_constraints,
        options={"maxiter": 500, "ftol": 1e-10},
    )

    if not result.success:
        # `check_feasible` has already established that the constraint set admits a
        # solution, so a failure here is numerical rather than structural. One retry
        # from a perturbed start is cheap; failing after it is honest.
        retry = minimize(
            negative_objective,
            _feasible_start(constraints, n, jitter=0.15),
            jac=gradient,
            method="SLSQP",
            bounds=bounds,
            constraints=scipy_constraints,
            options={"maxiter": 1000, "ftol": 1e-9},
        )
        if not retry.success:
            raise OptimizationFailedError(
                "The optimizer could not find a portfolio satisfying the constraints. "
                "This usually means the per-class limits leave no feasible allocation.",
                {"solver_message": str(result.message)},
            )
        result = retry

    weights = _tidy(np.asarray(result.x, dtype=float), constraints.max_weight)
    _verify(weights, constraints)

    expected_return = float(mu @ weights)
    expected_risk = float(np.sqrt(max(0.0, weights @ sigma @ weights)))

    return OptimizedPortfolio(
        symbols=symbols,
        weights=weights,
        expected_return=expected_return,
        expected_risk=expected_risk,
        objective_value=float(expected_return - lam * (weights @ sigma @ weights)),
    )


def _feasible_start(constraints: ConstraintSet, n: int, *, jitter: float = 0.0) -> np.ndarray:
    """A starting vector that already satisfies the class bands and the per-asset cap.

    Each class is seeded at its floor, the remainder is spread across classes by
    headroom, and each class's share is split evenly among its own assets. That
    lands inside the feasible region rather than somewhere the solver has to fight
    its way out of.
    """
    indices: dict[AssetClass, list[int]] = {}
    for position, cls in enumerate(constraints.class_of):
        indices.setdefault(cls, []).append(position)

    bands = constraints.class_bands
    allocation: dict[AssetClass, float] = {}

    for cls, positions in indices.items():
        floor, cap = bands.get(cls, (0.0, 1.0))
        # A class cannot hold more than its assets are individually allowed to.
        ceiling = min(cap, len(positions) * constraints.max_weight)
        allocation[cls] = min(floor, ceiling)

    remaining = 1.0 - sum(allocation.values())
    for _ in range(10):
        if remaining <= 1e-12:
            break
        headroom = {
            cls: min(bands.get(cls, (0.0, 1.0))[1], len(positions) * constraints.max_weight)
            - allocation[cls]
            for cls, positions in indices.items()
        }
        total_headroom = sum(max(0.0, value) for value in headroom.values())
        if total_headroom <= 1e-12:
            break
        for cls, available in headroom.items():
            if available <= 0:
                continue
            allocation[cls] += remaining * (available / total_headroom)
        # Re-clip, then loop: proportional spreading can overshoot a tight cap.
        for cls, positions in indices.items():
            ceiling = min(bands.get(cls, (0.0, 1.0))[1], len(positions) * constraints.max_weight)
            allocation[cls] = min(allocation[cls], ceiling)
        remaining = 1.0 - sum(allocation.values())

    weights = np.zeros(n)
    for cls, positions in indices.items():
        share = allocation[cls] / len(positions)
        for position in positions:
            weights[position] = min(share, constraints.max_weight)

    if jitter > 0:
        # Deterministic perturbation — a seeded generator, so a retry is still
        # reproducible for identical inputs.
        rng = np.random.default_rng(11)
        weights = np.clip(
            weights * (1.0 + rng.uniform(-jitter, jitter, n)), 0.0, constraints.max_weight
        )

    total = weights.sum()
    return weights / total if total > 0 else np.full(n, 1.0 / n)


def _tidy(weights: np.ndarray, max_weight: float) -> np.ndarray:
    """Clip to bounds, drop noise positions, renormalise to exactly 1."""
    weights = np.clip(weights, 0.0, max_weight)
    weights[weights < MIN_MEANINGFUL_WEIGHT] = 0.0

    total = weights.sum()
    if total <= 0:
        raise OptimizationFailedError(
            "The optimizer returned an empty portfolio — every position fell below the "
            "minimum meaningful weight."
        )

    weights = weights / total

    # Renormalising can push a position marginally over its cap. Spill the excess
    # into the positions with room, rather than returning weights that violate the
    # guarantee this function exists to make.
    for _ in range(10):
        excess = np.maximum(weights - max_weight, 0.0)
        if excess.sum() <= 1e-12:
            break
        weights = np.minimum(weights, max_weight)
        room = (weights > 0) & (weights < max_weight)
        if not room.any():
            break
        weights[room] += excess.sum() * (weights[room] / weights[room].sum())

    return weights


def _verify(weights: np.ndarray, constraints: ConstraintSet) -> None:
    """FR-11's acceptance criteria, checked on the actual answer."""
    total = float(weights.sum())
    if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
        raise OptimizationFailedError(
            f"Portfolio weights sum to {total:.6f}, outside the required 1.0 ± "
            f"{WEIGHT_SUM_TOLERANCE}.",
            {"weight_sum": total},
        )

    largest = float(weights.max()) if weights.size else 0.0
    if largest > constraints.max_weight + WEIGHT_SUM_TOLERANCE:
        raise OptimizationFailedError(
            f"A position of {largest:.4f} exceeds the {constraints.max_weight:.4f} per-asset cap.",
            {"max_weight_found": largest},
        )

    if (weights < -1e-9).any():
        raise OptimizationFailedError("The optimizer returned a negative weight.")

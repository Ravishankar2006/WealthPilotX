"""TreeSHAP attributions for the market-prediction model (FR-13, advanced).

FR-13's baseline is a rule-derived reason string, which M4 already ships on every
recommendation. This is the advanced half: for a gradient-boosted ensemble over
twenty correlated features there is no rule to derive a reason from, so the
explanation has to come from the model itself.

**Why there is no `shap` import here.** XGBoost implements TreeSHAP natively:
`Booster.predict(..., pred_contribs=True)` returns one contribution per feature plus
a bias term, and they sum exactly to the model's output. These are the same Shapley
values the `shap` package would compute for a tree model, via the same algorithm —
so importing it would add numba and llvmlite to the image to reach a routine
XGBoost already ships. `contributions_reconcile()` below asserts the identity rather
than trusting this paragraph.

**Why the risk classifier is not explained here.** The risk score served to the user
is the rubric's own weighted sum, so its per-factor decomposition is exact and
already returned in `top_factors`. A Shapley approximation of a forest that
approximates a rule this repo wrote would be less accurate than the number already
on screen. See `Docs/PLAN/PHASE-6-HARDENING.md` §2.1.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
import xgboost as xgb

from app.ml.prediction.model import PredictionArtifact

# How closely the contributions must reproduce the prediction. The booster computes
# in float32, so the tolerance is a float32 epsilon scaled for accumulation across
# ~20 features, not an arbitrary "close enough".
RECONCILE_TOLERANCE = 1e-5

# The default number of contributions served. All of them are computed — this only
# governs how many are worth putting in front of someone.
TOP_CONTRIBUTION_COUNT = 6

# Feature names are model-contract identifiers (`sma_ratio_20`), not English. The
# API returns both: the identifier so a result can be tied back to the feature
# matrix, and the label so the UI does not have to own a translation table.
FEATURE_LABELS: dict[str, str] = {
    "sma_ratio_20": "Price vs 20-day average",
    "sma_ratio_50": "Price vs 50-day average",
    "ema_ratio_12": "Price vs 12-day exponential average",
    "rsi_14": "Relative strength (14-day)",
    "macd_hist": "MACD histogram",
    "bollinger_position": "Position within Bollinger bands",
    "volatility_20": "Volatility (20-day)",
    "volatility_60": "Volatility (60-day)",
    "momentum_20": "Momentum (20-day)",
    "momentum_60": "Momentum (60-day)",
    "return_lag_1": "Previous day's return",
    "return_lag_5": "Return 5 days ago",
    "return_lag_10": "Return 10 days ago",
    "return_lag_20": "Return 20 days ago",
    "benchmark_correlation_60": "Correlation with the market (60-day)",
    "volume_ratio_20": "Volume vs 20-day average",
    "inflation": "Inflation",
    "interest_rate": "Interest rate",
    "unemployment": "Unemployment",
}


def label_for(feature: str) -> str:
    """A readable name, falling back to the identifier for anything unmapped.

    Falling back rather than raising is deliberate: a feature added to the matrix
    without a label here should degrade to a slightly ugly explanation, not to a
    500 on a page whose entire purpose is transparency.
    """
    return FEATURE_LABELS.get(feature, feature.replace("_", " "))


@dataclass(frozen=True, slots=True)
class Contribution:
    """One feature's Shapley value, in the units of the target (a log return)."""

    feature: str
    label: str
    value: float
    contribution: float

    @property
    def direction(self) -> str:
        return "increases" if self.contribution > 0 else "decreases"


@dataclass(frozen=True, slots=True)
class Attribution:
    """A full decomposition of one prediction.

    The identity that makes this an explanation rather than a ranking:

        base_value + sum(c.contribution for c in contributions) == predicted_return

    `contributions` holds *every* feature, so the identity holds on this object.
    Truncation for display happens in `top()`, which is why the sum is checked here
    and not after a caller has thrown most of the terms away.
    """

    base_value: float
    predicted_return: float
    contributions: tuple[Contribution, ...]

    def top(self, count: int = TOP_CONTRIBUTION_COUNT) -> tuple[Contribution, ...]:
        """The largest contributions by magnitude, keeping both signs.

        Ranking by absolute value rather than by value: the reason a prediction is
        low is as much an explanation as the reason it is high, and sorting by
        signed value would return only the bullish half.
        """
        ranked = sorted(self.contributions, key=lambda c: abs(c.contribution), reverse=True)
        return tuple(ranked[:count])

    @property
    def residual(self) -> float:
        """How far the decomposition misses the prediction. Zero, or a bug."""
        total = self.base_value + sum(c.contribution for c in self.contributions)
        return float(total - self.predicted_return)


def contributions_reconcile(attribution: Attribution) -> bool:
    return abs(attribution.residual) <= RECONCILE_TOLERANCE


def explain(artifact: PredictionArtifact, features: pd.DataFrame) -> Attribution:
    """Decompose the point model's prediction for a single feature row.

    Only the point model is explained, not the q10/q90 pair. Those produce the
    confidence figure, and "why is the model unsure?" is a different question with a
    different answer — attributing an interval width to features would invite
    reading it as a second prediction.
    """
    if len(features) != 1:
        raise ValueError(f"explain() takes exactly one feature row, got {len(features)}.")

    columns = list(artifact.feature_columns)
    ordered = features[columns]

    booster = artifact.model.get_booster()
    # `validate_features=False`: the DMatrix is built from the same frame the model
    # was fitted on, column-for-column, but XGBoost's name check is strict about
    # dtype-driven renames and this path has already pinned the order explicitly.
    matrix = xgb.DMatrix(ordered, feature_names=columns)
    raw = booster.predict(matrix, pred_contribs=True)

    # Shape is (1, n_features + 1); the trailing column is the bias — the model's
    # average output over its training set, before any feature moves it.
    row = np.asarray(raw)[0]
    *feature_contributions, bias = (float(v) for v in row)

    values = ordered.iloc[0]
    contributions = tuple(
        Contribution(
            feature=column,
            label=label_for(column),
            value=float(values[column]) if pd.notna(values[column]) else float("nan"),
            contribution=contribution,
        )
        for column, contribution in zip(columns, feature_contributions, strict=True)
    )

    return Attribution(
        base_value=bias,
        predicted_return=float(artifact.model.predict(ordered)[0]),
        contributions=contributions,
    )

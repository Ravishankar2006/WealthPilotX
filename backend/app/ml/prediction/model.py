"""Market prediction model (FR-08, FR-09).

Phase 3 plan, decision 2: XGBoost regression on the 20-day forward log return, with
a q10/q90 quantile pair supplying confidence.

Confidence deserves a word, because "confidence" is the easiest number in this
system to overstate. It is derived from the width of the model's own predictive
interval: a wide q10–q90 spread means the model's answer is unstable for this input,
and a narrow one means it is consistent. That is a statement about the model, not
about the market — it does not know whether it is right. FR-08 asks for confidence
"where supported"; this is what is honestly supportable.
"""

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import xgboost as xgb

from app.ml import evaluation
from app.ml.features.market import PREDICTION_HORIZON_DAYS
from app.ml.prediction.dataset import MIN_ROWS_PER_ASSET, TrainingData
from app.models.enums import TrendDirection

RANDOM_SEED = 20260301

# A dead band around zero, so the model can answer FLAT. Without it a predicted
# +0.02% would be reported as "UP" in the same vocabulary as a predicted 8% rally.
# 1% over a month is roughly the noise floor for the target.
TREND_DEAD_BAND = 0.01

# Deliberately conservative: the target is close to noise, and a deep unregularised
# booster will fit that noise perfectly and generalise not at all.
BOOSTER_PARAMS: dict[str, Any] = {
    "n_estimators": 400,
    "max_depth": 4,
    "learning_rate": 0.03,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 20,
    "reg_lambda": 2.0,
    "random_state": RANDOM_SEED,
    "n_jobs": -1,
    "tree_method": "hist",
}

# The spread that maps to zero confidence. A q10–q90 width of 20% of forward return
# is very wide for a one-month horizon; wider than this and the model is saying it
# does not know.
CONFIDENCE_SPREAD_CEILING = 0.20


@dataclass(frozen=True, slots=True)
class PredictionArtifact:
    model: xgb.XGBRegressor
    lower: xgb.XGBRegressor
    upper: xgb.XGBRegressor
    feature_columns: tuple[str, ...]
    horizon_days: int


@dataclass(frozen=True, slots=True)
class PredictionResult:
    predicted_return: float
    trend: TrendDirection
    confidence: float
    horizon_days: int


def _quantile_model(alpha: float) -> xgb.XGBRegressor:
    return xgb.XGBRegressor(objective="reg:quantileerror", quantile_alpha=alpha, **BOOSTER_PARAMS)


def train(data: TrainingData) -> tuple[PredictionArtifact, dict[str, Any]]:
    """Fit the point model and its quantile pair; evaluate on the held-out tail."""
    if data.is_empty:
        raise ValueError(
            "Not enough usable market history to train. Every tracked asset needs at "
            f"least {MIN_ROWS_PER_ASSET} rows after the indicator warm-up. Run "
            "`python -m app.jobs ingest-market --backfill-days 1100` and check "
            "`python -m app.jobs models` for what the last run saw."
        )

    model = xgb.XGBRegressor(objective="reg:squarederror", **BOOSTER_PARAMS)
    model.fit(data.x_train, data.y_train)

    lower = _quantile_model(0.1)
    lower.fit(data.x_train, data.y_train)
    upper = _quantile_model(0.9)
    upper.fit(data.x_train, data.y_train)

    predictions = model.predict(data.x_test)
    # Widened: the §18 metrics are floats, but the provenance fields added below
    # (ranges, feature list, split description) are not.
    metrics: dict[str, Any] = dict(evaluation.regression_metrics(data.y_test, predictions))

    baseline = evaluation.naive_baseline_prediction(data.y_train, len(data.y_test))
    baseline_metrics = evaluation.regression_metrics(data.y_test, baseline)

    metrics["baseline_rmse"] = baseline_metrics["rmse"]
    metrics["baseline_mae"] = baseline_metrics["mae"]
    # §10.5's first-release gate. Lower RMSE wins.
    metrics["beats_baseline"] = metrics["rmse"] < baseline_metrics["rmse"]

    metrics["horizon_days"] = PREDICTION_HORIZON_DAYS
    metrics["train_range"] = [str(data.train_start.date()), str(data.train_end.date())]
    metrics["test_range"] = [str(data.test_start.date()), str(data.test_end.date())]
    metrics["n_symbols"] = len(data.symbols)
    metrics["features"] = list(data.feature_columns)
    # §18 asks for the split methodology to be published with the results.
    metrics["split_method"] = (
        f"Chronological with a {PREDICTION_HORIZON_DAYS * 2}-day purge between train and "
        "test, so no training row's forward-return window overlaps the test period."
    )

    artifact = PredictionArtifact(
        model=model,
        lower=lower,
        upper=upper,
        # The columns actually fitted on, not the declared set — see
        # `features.market.usable_feature_columns`. Inference reads this back so a
        # macro column arriving later cannot shift the positions the booster indexes.
        feature_columns=data.feature_columns,
        horizon_days=PREDICTION_HORIZON_DAYS,
    )
    return artifact, metrics


def _trend(value: float) -> TrendDirection:
    if value > TREND_DEAD_BAND:
        return TrendDirection.UP
    if value < -TREND_DEAD_BAND:
        return TrendDirection.DOWN
    return TrendDirection.FLAT


def _confidence(spread: float) -> float:
    """Map a q10–q90 spread to [0, 1]. Narrower is more confident."""
    if not np.isfinite(spread) or spread < 0:
        return 0.0
    return round(float(max(0.0, 1.0 - (spread / CONFIDENCE_SPREAD_CEILING))), 4)


def predict(artifact: PredictionArtifact, features: pd.DataFrame) -> PredictionResult:
    """Predict for a single feature row."""
    ordered = features[list(artifact.feature_columns)]

    value = float(artifact.model.predict(ordered)[0])
    low = float(artifact.lower.predict(ordered)[0])
    high = float(artifact.upper.predict(ordered)[0])

    return PredictionResult(
        predicted_return=round(value, 8),
        trend=_trend(value),
        confidence=_confidence(high - low),
        horizon_days=artifact.horizon_days,
    )


def predict_batch(artifact: PredictionArtifact, features: pd.DataFrame) -> np.ndarray:
    """Batch point predictions, for the §20 fixed-evaluation-set regression test."""
    return artifact.model.predict(features[list(artifact.feature_columns)])

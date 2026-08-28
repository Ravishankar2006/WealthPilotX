"""Market prediction read path and the FR-09 asset metrics.

FR-09 requires all six metrics to be "returned or explicitly marked unavailable with
a reason". A newly tracked symbol has too little history for the 60-day indicators,
and a stack with no promoted model has no prediction at all — both are ordinary
states, and both are reported as such rather than as an error or, worse, as an
imputed number that looks like a measurement.
"""

import uuid
from datetime import date

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ml import registry
from app.ml.features import market as market_features
from app.ml.features import technical
from app.ml.prediction import model as prediction_model
from app.models.asset import Asset
from app.models.model_record import PREDICTION_MODEL
from app.models.prediction import Prediction
from app.schemas.risk import PredictionOut


class NoPredictionError(Exception):
    """Raised when an asset has no stored prediction. Translated to a 404 by the
    route, which is the honest status: the asset exists, this result does not."""

    def __init__(self, symbol: str, reason: str) -> None:
        super().__init__(reason)
        self.symbol = symbol
        self.reason = reason


def latest_prediction(db: Session, asset_id: uuid.UUID) -> Prediction | None:
    return db.scalar(
        select(Prediction)
        .where(Prediction.asset_id == asset_id)
        .order_by(Prediction.prediction_date.desc(), Prediction.created_at.desc())
        .limit(1)
    )


def _asset_metrics(db: Session, symbol: str) -> tuple[dict[str, float], list[str]]:
    """FR-09's non-model metrics, computed from stored prices."""
    prices = market_features.load_prices(db, symbol)
    unavailable: list[str] = []

    if prices.empty or len(prices) < 60:
        return {}, ["volatility", "momentum", "risk_score"]

    close = prices["adj_close"]
    volatility = technical.volatility(close, 60).iloc[-1]
    momentum = technical.momentum(close, 60).iloc[-1]

    metrics: dict[str, float] = {}
    if np.isfinite(volatility):
        metrics["volatility"] = round(float(volatility), 6)
        # FR-09's "risk" for an asset: annualised volatility mapped onto [0, 1]
        # against a 40% ceiling, which is roughly where a single equity sits at the
        # high end. A bounded number is what the M4 optimizer can compare across
        # assets; the raw volatility is reported alongside it for anyone who wants it.
        metrics["risk_score"] = round(min(float(volatility) / 0.40, 1.0), 4)
    else:
        unavailable.extend(["volatility", "risk_score"])

    if np.isfinite(momentum):
        metrics["momentum"] = round(float(momentum), 6)
    else:
        unavailable.append("momentum")

    return metrics, unavailable


def asset_prediction(db: Session, asset: Asset) -> PredictionOut:
    """Assemble FR-08's prediction and FR-09's metrics for one asset."""
    metrics, unavailable = _asset_metrics(db, asset.symbol)
    stored = latest_prediction(db, asset.id)

    if stored is None:
        # No stored prediction: either no model is promoted yet, or the predict job
        # has not run for this asset. Say which — they need different fixes, and
        # "no prediction" alone sends an operator looking in the wrong place.
        has_model = registry.production_record(db, PREDICTION_MODEL) is not None
        reason = (
            "no prediction has been generated for this asset yet"
            if has_model
            else "no production prediction model is available"
        )
        raise NoPredictionError(asset.symbol, reason)

    return PredictionOut(
        symbol=asset.symbol,
        prediction_date=stored.prediction_date,
        horizon_days=stored.horizon_days,
        predicted_return=stored.predicted_return,
        trend=stored.trend,
        confidence=stored.confidence,
        model_version=stored.model_version,
        expected_return=stored.predicted_return,
        volatility=metrics.get("volatility"),
        momentum=metrics.get("momentum"),
        risk_score=metrics.get("risk_score"),
        unavailable=unavailable,
    )


def generate_for_asset(
    db: Session,
    asset: Asset,
    artifact: prediction_model.PredictionArtifact,
    model_version: str,
    *,
    as_of: date | None = None,
) -> Prediction | None:
    """Compute and persist one asset's prediction. None when history is too short."""
    built = market_features.build_inference_row(
        db, asset.symbol, as_of=as_of, feature_columns=artifact.feature_columns
    )
    if built is None:
        return None

    features, feature_date = built
    result = prediction_model.predict(artifact, features)

    return Prediction(
        asset_id=asset.id,
        model_version=model_version,
        prediction_date=feature_date,
        predicted_return=result.predicted_return,
        trend=result.trend,
        confidence=result.confidence,
        horizon_days=result.horizon_days,
    )

"""Read path for the prediction-explanation endpoint (FR-13, advanced).

The rule this module exists to enforce: **explain the prediction that was served,
with the model that served it.** The obvious shortcut — load whatever is in
production now, recompute today's features, decompose that — produces a tidy answer
to a question nobody asked. A user looking at a prediction made last Tuesday by
v3 gets shown why v4 would predict something today, with no indication that the
number moved.

So the version travels with the stored prediction row, the features are rebuilt as
of that prediction's own date, and the recomputed value is checked against the
stored one. When they disagree the response says so rather than quietly presenting
a decomposition of a different number.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger, safe_extra
from app.ml import artifacts, explain, registry
from app.ml.features import market as market_features
from app.ml.prediction.model import PredictionArtifact
from app.models.asset import Asset
from app.models.model_record import PREDICTION_MODEL
from app.services.prediction_service import NoPredictionError, latest_prediction

logger = get_logger(__name__)

# How far the recomputed prediction may sit from the stored one before the
# explanation is marked unreproduced. Generous next to `explain`'s reconciliation
# tolerance, because this comparison crosses a float32 model, a NUMERIC(12, 8)
# database column and a feature rebuild — the question is "is this the same
# prediction?", not "is this bit-identical?".
REPRODUCTION_TOLERANCE = 1e-6


@dataclass(frozen=True, slots=True)
class PredictionAttribution:
    symbol: str
    prediction_date: date
    horizon_days: int
    predicted_return: float
    stored_return: float
    base_value: float
    model_version: str
    contributions: tuple[explain.Contribution, ...]
    contributions_total: int
    reproduced: bool


def _load_artifact(db: Session, version: str) -> PredictionArtifact:
    """Load the exact model version that produced a stored prediction.

    Three ways this legitimately fails, all of them operational rather than
    exceptional: the registry row was pruned, the artifact file is gone from the
    volume, or the file no longer matches its checksum. All three mean the same
    thing to a caller — this prediction can no longer be explained — and none of
    them mean the request was bad. 503, with the reason in the message.
    """
    try:
        record = registry.get_record(db, PREDICTION_MODEL, version)
    except AppError as exc:
        raise AppError(
            503,
            "explanation_unavailable",
            f"Model version {version!r} is no longer registered, so the prediction "
            "it produced cannot be explained.",
        ) from exc

    try:
        payload = artifacts.load(record.artifact_path, record.artifact_checksum)
    except (FileNotFoundError, ValueError) as exc:
        logger.warning(
            "explanation_artifact_unavailable",
            extra=safe_extra(model_version=version, reason=str(exc)),
        )
        raise AppError(
            503,
            "explanation_unavailable",
            f"The stored artifact for model version {version!r} could not be loaded, "
            "so the prediction it produced cannot be explained.",
        ) from exc

    if not isinstance(payload, PredictionArtifact):
        raise AppError(
            503,
            "explanation_unavailable",
            f"Model version {version!r} is not a market-prediction artifact.",
        )
    return payload


def explain_prediction(db: Session, asset: Asset) -> PredictionAttribution:
    """Decompose the latest stored prediction for one asset."""
    stored = latest_prediction(db, asset.id)
    if stored is None:
        has_model = registry.production_record(db, PREDICTION_MODEL) is not None
        raise NoPredictionError(
            asset.symbol,
            "no prediction has been generated for this asset yet"
            if has_model
            else "no production prediction model is available",
        )

    artifact = _load_artifact(db, stored.model_version)

    built = market_features.build_inference_row(
        db,
        asset.symbol,
        as_of=stored.prediction_date,
        feature_columns=artifact.feature_columns,
    )
    if built is None:
        # The prediction exists but its inputs no longer do — history trimmed, or an
        # asset whose warm-up window has been rewritten. Not a 404: the prediction
        # is right there. It just cannot be taken apart any more.
        raise AppError(
            503,
            "explanation_unavailable",
            f"The feature history behind {asset.symbol}'s prediction is no longer "
            "complete, so it cannot be decomposed.",
        )

    features, _ = built
    attribution = explain.explain(artifact, features)

    if not explain.contributions_reconcile(attribution):
        # The Shapley identity failing is a library-level bug, not a data condition.
        # Serving the contributions anyway would present numbers that do not add up
        # as if they explained something.
        logger.error(
            "shap_contributions_do_not_reconcile",
            extra=safe_extra(symbol=asset.symbol, residual=attribution.residual),
        )
        raise AppError(
            503,
            "explanation_unavailable",
            "The feature contributions did not reconcile with the prediction, so "
            "they are not being served.",
        )

    stored_return = float(stored.predicted_return)
    reproduced = abs(attribution.predicted_return - stored_return) <= REPRODUCTION_TOLERANCE

    if not reproduced:
        logger.warning(
            "prediction_not_reproduced",
            extra=safe_extra(
                symbol=asset.symbol,
                model_version=stored.model_version,
                stored=stored_return,
                recomputed=attribution.predicted_return,
            ),
        )

    return PredictionAttribution(
        symbol=asset.symbol,
        prediction_date=stored.prediction_date,
        horizon_days=stored.horizon_days,
        predicted_return=attribution.predicted_return,
        stored_return=stored_return,
        base_value=attribution.base_value,
        model_version=stored.model_version,
        contributions=attribution.top(),
        contributions_total=len(attribution.contributions),
        reproduced=reproduced,
    )

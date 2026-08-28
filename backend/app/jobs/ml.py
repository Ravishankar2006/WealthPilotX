"""ML job implementations (§10.5).

Kept out of `__main__.py` because these import the ML stack, and the CLI's other
commands — `seed-assets`, `ingest-market` — have no business paying that import cost
just to parse arguments.

Training and promotion are separate commands on purpose. §10.5 requires manual
review before promotion in the MVP, and a training script that promotes what it just
built is how an unreviewed model reaches users.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger, safe_extra
from app.ml import registry
from app.ml.prediction import dataset as prediction_dataset
from app.ml.prediction import model as prediction_model
from app.ml.risk import model as risk_model
from app.models.asset import Asset
from app.models.model_record import PREDICTION_MODEL, RISK_MODEL, ModelRecord
from app.models.prediction import Prediction
from app.services import prediction_service

logger = get_logger("app.jobs.ml")


def _summarise(record: ModelRecord) -> None:
    metrics = record.metrics or {}
    headline = {
        key: value
        for key, value in metrics.items()
        if isinstance(value, (int, float, bool)) and key != "n_samples"
    }
    print(f"{record.name}:{record.version} registered as {record.status}")  # noqa: T201
    for key, value in sorted(headline.items()):
        print(f"  {key}: {value}")  # noqa: T201
    if not metrics.get("beats_baseline", True):
        print("  ⚠ does not beat the naive baseline — promotion will be refused")  # noqa: T201


def train_risk(db: Session) -> ModelRecord:
    """Train the FR-03 classifier on the rubric-labelled synthetic population."""
    settings = get_settings()
    artifact, metrics = risk_model.train(population=settings.risk_training_population)

    record = registry.register(
        db,
        name=RISK_MODEL,
        payload=artifact,
        metrics=metrics,
        # Synthetic data has no calendar range — the population is drawn, not
        # observed, so leaving these null is the honest answer.
        training_start=None,
        training_end=None,
    )
    _summarise(record)
    print(  # noqa: T201
        "  note: metrics measure fidelity to app/ml/risk/rubric.py, not correctness "
        "about real people"
    )
    return record


def train_prediction(db: Session) -> ModelRecord:
    """Train the FR-08 regressor on stored market history."""
    data = prediction_dataset.build_training_data(db)
    artifact, metrics = prediction_model.train(data)

    record = registry.register(
        db,
        name=PREDICTION_MODEL,
        payload=artifact,
        metrics=metrics,
        training_start=data.train_start.date(),
        training_end=data.train_end.date(),
    )
    _summarise(record)
    return record


def generate_predictions(db: Session) -> int:
    """Write a prediction row per tracked asset (FR-08).

    Idempotent on `(asset_id, prediction_date, model_version)`, like ingestion — so
    a re-run after a partial failure is safe rather than duplicating rows.
    """
    loaded = registry.resolve_production(db, PREDICTION_MODEL)
    artifact: prediction_model.PredictionArtifact = loaded.payload

    assets = list(db.scalars(select(Asset).where(Asset.is_active.is_(True)).order_by(Asset.symbol)))
    rows: list[dict[str, object]] = []
    skipped: list[str] = []

    for asset in assets:
        prediction = prediction_service.generate_for_asset(db, asset, artifact, loaded.version)
        if prediction is None:
            # Too little history for the 60-day indicators. FR-09 requires this to be
            # reported rather than filled in.
            skipped.append(asset.symbol)
            continue
        rows.append(
            {
                "asset_id": prediction.asset_id,
                "model_version": prediction.model_version,
                "prediction_date": prediction.prediction_date,
                "predicted_return": prediction.predicted_return,
                "trend": prediction.trend,
                "confidence": prediction.confidence,
                "horizon_days": prediction.horizon_days,
                "created_at": datetime.now(UTC),
            }
        )

    written = 0
    if rows:
        statement = insert(Prediction).values(rows)
        statement = statement.on_conflict_do_update(
            constraint="uq_prediction_asset_date_model",
            set_={
                "predicted_return": statement.excluded.predicted_return,
                "trend": statement.excluded.trend,
                "confidence": statement.excluded.confidence,
                "created_at": statement.excluded.created_at,
            },
        )
        # RETURNING rather than rowcount — psycopg reports -1 for a multi-row INSERT,
        # the same trap that made M2's ingestion report negative row counts.
        written = len(db.execute(statement.returning(Prediction.id)).all())
        db.commit()

    logger.info(
        "predictions_generated",
        extra=safe_extra(
            model_version=loaded.version, rows=written, skipped=len(skipped), assets=len(assets)
        ),
    )
    print(  # noqa: T201
        f"predict: {written} predictions from {loaded.record.name}:{loaded.version}"
        + (
            f"; skipped {len(skipped)} with insufficient history: {', '.join(skipped)}"
            if skipped
            else ""
        )
    )
    return written


def promote(db: Session, name: str, version: str, *, force: bool = False) -> ModelRecord:
    record = registry.promote(db, name, version, force=force)
    print(f"promote: {record.name}:{record.version} is now {record.status}")  # noqa: T201
    return record


def list_models(db: Session, name: str | None = None) -> list[ModelRecord]:
    statement = select(ModelRecord).order_by(ModelRecord.name, ModelRecord.created_at)
    if name:
        statement = statement.where(ModelRecord.name == name)
    records = list(db.scalars(statement))

    for record in records:
        metric_name, _ = registry.PROMOTION_METRIC.get(record.name, ("", True))
        value = (record.metrics or {}).get(metric_name)
        rendered = f"{value:.6f}" if isinstance(value, (int, float)) else "—"
        print(  # noqa: T201
            f"{record.name:20} {record.version:6} {str(record.status):11} {metric_name}={rendered}"
        )
    if not records:
        print("no models registered")  # noqa: T201
    return records

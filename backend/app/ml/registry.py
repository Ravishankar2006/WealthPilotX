"""Model registry operations (§10.5).

The lifecycle this enforces:

    train  →  EXPERIMENT row + artifact
    promote →  compare against the incumbent on the held-out test set
               ↳ wins  → new row PRODUCTION, old row RETIRED
               ↳ loses → refused, nothing changes
    serve  →  resolve_production(name)

**Promotion is never a side effect of training.** §10.5 requires manual review
before promotion in the MVP, and a training script that promotes on its own is how
an unreviewed model reaches users. `train` writes EXPERIMENT and stops.

The comparison metric is fixed per model rather than chosen per promotion, because a
metric picked after seeing the results is not a gate.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger, safe_extra
from app.ml import artifacts
from app.models.enums import ModelStatus
from app.models.model_record import PREDICTION_MODEL, RISK_MODEL, ModelRecord

logger = get_logger(__name__)

# The metric each model is promoted on, and whether higher wins. Declared up front so
# a promotion cannot be justified by whichever number happened to improve.
PROMOTION_METRIC: dict[str, tuple[str, bool]] = {
    RISK_MODEL: ("f1_macro", True),
    # RMSE rather than R²: R² against a near-zero-signal target is unstable and can
    # be negative, which makes "higher is better" comparisons behave strangely.
    PREDICTION_MODEL: ("rmse", False),
}


class ModelNotAvailableError(AppError):
    """No production model. A 503, not a 500 — the system is fine, it is just not
    ready to serve this surface yet, and the operator action is to promote one."""

    def __init__(self, name: str) -> None:
        super().__init__(
            503,
            "model_unavailable",
            f"No production model is registered for {name!r}. "
            "Train and promote one before requesting this result.",
        )


@dataclass(frozen=True, slots=True)
class LoadedModel:
    record: ModelRecord
    payload: Any

    @property
    def version(self) -> str:
        return self.record.version


def next_version(db: Session, name: str) -> str:
    """Monotonic `v1`, `v2`, … per model name.

    Simple on purpose: the git commit and training range on the row carry the
    provenance §10.5 asks for, so the version string only has to be unique and
    ordered.
    """
    count = len(list(db.scalars(select(ModelRecord.id).where(ModelRecord.name == name))))
    return f"v{count + 1}"


def register(
    db: Session,
    *,
    name: str,
    payload: Any,
    metrics: dict[str, Any],
    training_start: date | None,
    training_end: date | None,
) -> ModelRecord:
    """Persist an artifact and its EXPERIMENT row."""
    version = next_version(db, name)
    path, digest = artifacts.save(payload, name, version)

    record = ModelRecord(
        name=name,
        version=version,
        training_start=training_start,
        training_end=training_end,
        metrics=metrics,
        status=ModelStatus.EXPERIMENT,
        artifact_path=str(path),
        artifact_checksum=digest,
        git_commit=artifacts.git_commit(),
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    logger.info(
        "model_registered",
        extra=safe_extra(model=name, version=version, status="EXPERIMENT", metrics=metrics),
    )
    return record


def production_record(db: Session, name: str) -> ModelRecord | None:
    return db.scalar(
        select(ModelRecord)
        .where(ModelRecord.name == name, ModelRecord.status == ModelStatus.PRODUCTION)
        .order_by(ModelRecord.created_at.desc())
        .limit(1)
    )


def get_record(db: Session, name: str, version: str) -> ModelRecord:
    record = db.scalar(
        select(ModelRecord).where(ModelRecord.name == name, ModelRecord.version == version)
    )
    if record is None:
        raise AppError(404, "model_not_found", f"No model {name!r} version {version!r}.")
    return record


def resolve_production(db: Session, name: str) -> LoadedModel:
    """Load the production model, verifying its artifact checksum."""
    record = production_record(db, name)
    if record is None:
        raise ModelNotAvailableError(name)

    payload = artifacts.load(record.artifact_path, record.artifact_checksum)
    return LoadedModel(record=record, payload=payload)


def _metric_of(record: ModelRecord, metric: str) -> float | None:
    if not record.metrics:
        return None
    value = record.metrics.get(metric)
    return float(value) if isinstance(value, (int, float)) else None


def _beats(candidate: float, incumbent: float, higher_is_better: bool) -> bool:
    return candidate > incumbent if higher_is_better else candidate < incumbent


def promote(db: Session, name: str, version: str, *, force: bool = False) -> ModelRecord:
    """Promote a model, refusing a regression against the incumbent (§10.5).

    First promotion has no incumbent; the model is instead required to have beaten a
    naive baseline during training, which the trainer records under
    `beats_baseline`. §10.5 asks for exactly that on the first release.

    `force` exists for the case where a metric genuinely stops being comparable —
    a changed target or feature set — and is logged loudly, because that is the path
    by which a worse model reaches production.
    """
    candidate = get_record(db, name, version)
    if candidate.status is ModelStatus.PRODUCTION:
        return candidate

    metric, higher_is_better = PROMOTION_METRIC[name]
    candidate_value = _metric_of(candidate, metric)
    if candidate_value is None and not force:
        raise AppError(
            409,
            "promotion_refused",
            f"Model {name}:{version} has no {metric!r} metric recorded, so it cannot be "
            "compared against the incumbent.",
        )

    incumbent = production_record(db, name)

    if incumbent is None:
        beats_baseline = bool((candidate.metrics or {}).get("beats_baseline"))
        if not beats_baseline and not force:
            raise AppError(
                409,
                "promotion_refused",
                f"Model {name}:{version} did not beat the naive baseline, so it cannot be "
                "the first production model (§10.5).",
            )
    else:
        incumbent_value = _metric_of(incumbent, metric)
        if (
            incumbent_value is not None
            and candidate_value is not None
            and not _beats(candidate_value, incumbent_value, higher_is_better)
            and not force
        ):
            raise AppError(
                409,
                "promotion_refused",
                f"Model {name}:{version} does not beat the incumbent {incumbent.version} on "
                f"{metric} ({candidate_value:.6f} vs {incumbent_value:.6f}). Not promoted.",
            )
        incumbent.status = ModelStatus.RETIRED

    candidate.status = ModelStatus.PRODUCTION
    db.commit()
    db.refresh(candidate)

    logger.info(
        "model_promoted",
        extra=safe_extra(
            model=name,
            version=version,
            metric=metric,
            value=candidate_value,
            replaced=incumbent.version if incumbent else None,
            forced=force,
        ),
    )
    return candidate

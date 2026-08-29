"""Drift observations (§10.5's "basic drift monitoring").

Not in PRD §12, required by §10.5, and for the same reason `ingestion_runs` exists:
"alert if either shifts beyond a defined threshold" needs somewhere to compare
*against*. A log line alerts once and is then gone; a table lets the next run ask
whether this is the third week in a row.

One row per measured subject per run — a feature name for a stability check, the
model itself for an error check — rather than one row per run with a JSON blob. The
question a reader asks is "which feature moved, and for how long?", and that is a
query over rows, not a scan through blobs.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, Enum, Float, Index, String, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_at_column, uuid_pk
from app.models.enums import DriftCheck, DriftVerdict


class ModelMonitoring(Base):
    __tablename__ = "model_monitoring"
    __table_args__ = (
        Index(
            "ix_model_monitoring_model_created_desc",
            "model_name",
            text("created_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()

    model_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # The version the check was run against. §10.5 ties every served result to a
    # model version; a drift measurement is a statement about one specific model,
    # and carrying the version is what stops a promotion silently resetting the
    # history a trend is read from.
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)

    check: Mapped[DriftCheck] = mapped_column(
        Enum(DriftCheck, name="drift_check"), nullable=False, index=True
    )
    # Feature name for a stability check; the model name for an error check.
    subject: Mapped[str] = mapped_column(String(100), nullable=False)

    # Nullable because INSUFFICIENT_DATA rows have no measurement — that is what
    # they record. A sentinel like 0.0 would be indistinguishable from a perfectly
    # stable feature.
    value: Mapped[float | None] = mapped_column(Float)
    verdict: Mapped[DriftVerdict] = mapped_column(
        Enum(DriftVerdict, name="drift_verdict"), nullable=False, index=True
    )

    reference_start: Mapped[date | None] = mapped_column(Date)
    reference_end: Mapped[date | None] = mapped_column(Date)
    window_start: Mapped[date | None] = mapped_column(Date)
    window_end: Mapped[date | None] = mapped_column(Date)

    # Sample sizes, thresholds in force, and the reason when a check could not run.
    # Stored so a row stays interpretable after the thresholds are next changed.
    details: Mapped[dict[str, Any] | None] = mapped_column(postgresql.JSONB)

    created_at: Mapped[datetime] = created_at_column()

    def __repr__(self) -> str:
        return f"<ModelMonitoring {self.check} {self.subject} {self.verdict}>"

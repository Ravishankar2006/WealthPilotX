"""Ingestion bookkeeping — not in PRD §12, required by FR-04 and FR-06.

FR-04 requires a provider outage to surface "a health-check alert rather than
silently skipping the day", and FR-06 requires the data-quality report to be
"logged for audit". Neither is satisfiable by log lines alone: `/health` cannot
query stdout, and an auditor cannot join against it. This table is the queryable
record both requirements need.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, Index, Integer, String, Text, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, uuid_pk
from app.models.enums import IngestionStatus

MARKET_JOB = "ingest_market"
ECONOMIC_JOB = "ingest_economic"


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    __table_args__ = (
        # What /health queries: the newest run for a job, and the newest successful one.
        Index("ix_ingestion_runs_job_started_desc", "job", text("started_at DESC")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    job: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status: Mapped[IngestionStatus] = mapped_column(
        Enum(IngestionStatus, name="ingestion_status"), nullable=False, index=True
    )

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    rows_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    symbols_ok: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    symbols_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # The FR-06 quality report: row counts, null rate, duplicate and outlier counts.
    quality: Mapped[dict[str, Any] | None] = mapped_column(postgresql.JSONB)

    # Failure summary only — an exception type and message, never a provider payload,
    # which could carry values we have no business persisting.
    error: Mapped[str | None] = mapped_column(Text)

    # Ties the run back to its log lines (§16.4).
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)

    def __repr__(self) -> str:
        return f"<IngestionRun {self.job} {self.status}>"

"""Ingestion run bookkeeping and the health summary it feeds (FR-04, §16.4).

FR-04's second acceptance criterion is the reason this module exists: a provider
outage must "surface a health-check alert rather than silently skipping the day".
That needs two things a log line cannot give you — a record `/health` can query, and
a status vocabulary in which "most symbols worked" is not "fine".
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import correlation_id, get_logger, safe_extra
from app.models.enums import IngestionStatus
from app.models.ingestion_run import ECONOMIC_JOB, MARKET_JOB, IngestionRun
from app.models.market_data import MarketData

logger = get_logger(__name__)

TRACKED_JOBS = (MARKET_JOB, ECONOMIC_JOB)


def start_run(db: Session, job: str) -> IngestionRun:
    """Open a RUNNING row before any provider call.

    Written first and committed immediately so that a job killed mid-flight leaves a
    RUNNING row behind rather than no trace at all. A stuck RUNNING run is a visible
    problem; a missing run looks exactly like a job that was never scheduled.
    """
    run = IngestionRun(
        job=job,
        status=IngestionStatus.RUNNING,
        started_at=datetime.now(UTC),
        correlation_id=correlation_id.get(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def finish_run(
    db: Session,
    run: IngestionRun,
    *,
    status: IngestionStatus,
    rows_written: int = 0,
    symbols_ok: int = 0,
    symbols_failed: int = 0,
    quality: dict[str, Any] | None = None,
    error: str | None = None,
) -> IngestionRun:
    run.status = status
    run.finished_at = datetime.now(UTC)
    run.rows_written = rows_written
    run.symbols_ok = symbols_ok
    run.symbols_failed = symbols_failed
    run.quality = quality
    run.error = error
    db.commit()
    db.refresh(run)

    # The FR-06 audit trail: the quality report goes to the log as well as the table,
    # so it is present in whatever log aggregation exists even if the database is the
    # thing that is unwell.
    log = logger.error if status is IngestionStatus.FAILED else logger.info
    log(
        "ingestion_run_finished",
        extra=safe_extra(
            job=run.job,
            status=str(status),
            rows_written=rows_written,
            symbols_ok=symbols_ok,
            symbols_failed=symbols_failed,
            duration_seconds=round((run.finished_at - run.started_at).total_seconds(), 2),
            quality=quality,
            error=error,
        ),
    )
    return run


def latest_run(db: Session, job: str) -> IngestionRun | None:
    return db.scalar(
        select(IngestionRun)
        .where(IngestionRun.job == job)
        .order_by(IngestionRun.started_at.desc())
        .limit(1)
    )


def last_successful_run(db: Session, job: str) -> IngestionRun | None:
    return db.scalar(
        select(IngestionRun)
        .where(
            IngestionRun.job == job,
            IngestionRun.status.in_((IngestionStatus.SUCCESS, IngestionStatus.PARTIAL)),
        )
        .order_by(IngestionRun.started_at.desc())
        .limit(1)
    )


@dataclass(frozen=True, slots=True)
class JobHealth:
    job: str
    last_status: str
    last_run_at: str | None
    last_success_at: str | None
    stale: bool
    healthy: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "job": self.job,
            "last_status": self.last_status,
            "last_run_at": self.last_run_at,
            "last_success_at": self.last_success_at,
            "stale": self.stale,
            "healthy": self.healthy,
        }


NEVER_RUN = "never_run"


def job_health(db: Session, job: str, *, stale_after_hours: int) -> JobHealth:
    latest = latest_run(db, job)

    if latest is None:
        # A job that has never run is not a failure — it is a fresh install, or a
        # deployment whose scheduler has not reached its first window yet. Calling
        # that "degraded" would make the alert meaningless on day one.
        return JobHealth(job, NEVER_RUN, None, None, stale=False, healthy=True)

    success = last_successful_run(db, job)
    cutoff = datetime.now(UTC) - timedelta(hours=stale_after_hours)
    last_success_at = success.started_at if success else None
    stale = last_success_at is None or last_success_at < cutoff

    return JobHealth(
        job=job,
        last_status=str(latest.status),
        last_run_at=latest.started_at.isoformat(),
        last_success_at=last_success_at.isoformat() if last_success_at else None,
        stale=stale,
        healthy=latest.status is IngestionStatus.SUCCESS and not stale,
    )


def ingestion_health(db: Session, *, stale_after_hours: int) -> dict[str, Any]:
    """The `ingestion` block on `GET /api/v1/health`."""
    jobs = [job_health(db, job, stale_after_hours=stale_after_hours) for job in TRACKED_JOBS]
    latest_date = db.scalar(select(func.max(MarketData.date)))

    return {
        "healthy": all(job.healthy for job in jobs),
        "latest_market_date": latest_date.isoformat() if latest_date else None,
        "jobs": [job.as_dict() for job in jobs],
    }


def run_summary(run: IngestionRun) -> dict[str, Any]:
    return {
        "id": str(run.id) if isinstance(run.id, uuid.UUID) else run.id,
        "job": run.job,
        "status": str(run.status),
        "rows_written": run.rows_written,
        "symbols_ok": run.symbols_ok,
        "symbols_failed": run.symbols_failed,
    }

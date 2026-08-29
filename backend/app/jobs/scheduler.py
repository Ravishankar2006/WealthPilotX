"""Scheduler process — the entrypoint for the `scheduler` container.

Runs in its own container, not inside the API (Phase 2 plan, decision 2). Two
reasons that decision holds up: a scheduler embedded in the API would fire once per
replica the moment the API scales (§16.4), and ingestion would compete with request
handling for the same process.

Jobs are invoked in-process rather than by shelling out to `python -m app.jobs`.
Same code path, no subprocess to supervise, and a crash is a traceback in this
container's logs instead of a lost exit code.
"""

import signal
import sys
import threading
from types import FrameType

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger, safe_extra
from app.jobs.__main__ import main as run_job

logger = get_logger("app.jobs.scheduler")

# All times UTC.
#
# Market: 23:30, comfortably after the 21:00 UTC US close and after the provider has
# settled the day's final bars. Running at the close itself reliably fetches a
# provisional last bar that then needs correcting.
#
# Economic: 12:30, before the US morning. FRED publishes on its own schedule and
# FR-05 only requires storage within 24 hours of publication, so a daily sweep with
# a year-long lookback catches both new points and revisions.
#
# Monitoring: 01:00, an hour and a half after the market job, so the drift check
# reads the day that has just landed rather than yesterday's. Daily rather than
# weekly because §10.5 allows a drift alert to *trigger* retraining, and an alert a
# week stale is one that arrives after the decision it should have informed.
MARKET_CRON = CronTrigger(hour=23, minute=30, timezone="UTC")
ECONOMIC_CRON = CronTrigger(hour=12, minute=30, timezone="UTC")
MONITOR_CRON = CronTrigger(hour=1, minute=0, timezone="UTC")


def _run(command: str) -> None:
    """Invoke a job and swallow nothing.

    APScheduler removes a job whose callable raises, which would silently disable
    ingestion after one bad night — the exact failure mode FR-04 forbids. So the
    exception is logged here and not re-raised, leaving the schedule intact; the
    failure is already recorded in `ingestion_runs` and visible on `/health`.
    """
    logger.info("scheduled_job_starting", extra=safe_extra(job=command))
    try:
        code = run_job([command])
    except Exception:
        logger.exception("scheduled_job_crashed", extra=safe_extra(job=command))
        return
    logger.info("scheduled_job_finished", extra=safe_extra(job=command, exit_code=code))


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        _run,
        MARKET_CRON,
        args=["ingest-market"],
        id="ingest-market",
        # A run that overruns its next window must not stack up a second copy
        # against the same provider.
        max_instances=1,
        # After downtime, run once for the missed windows rather than replaying each.
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _run,
        ECONOMIC_CRON,
        args=["ingest-economic"],
        id="ingest-economic",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _run,
        MONITOR_CRON,
        args=["monitor"],
        id="monitor",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    return scheduler


def main() -> int:
    settings = get_settings()
    configure_logging("DEBUG" if settings.debug else "INFO")

    scheduler = build_scheduler()

    def _shutdown(signum: int, _frame: FrameType | None) -> None:
        logger.info("scheduler_stopping", extra=safe_extra(signal=signum))
        # Non-blocking: this runs on the main thread, which is the thread
        # `scheduler.start()` is blocking, so waiting for jobs here would deadlock.
        threading.Thread(target=lambda: scheduler.shutdown(wait=False), daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    logger.info(
        "scheduler_started",
        extra=safe_extra(
            market_provider=settings.market_data_provider,
            economic_provider=settings.economic_data_provider,
        ),
    )
    scheduler.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())

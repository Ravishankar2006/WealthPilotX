"""Economic data ingestion (FR-05).

Acceptance criterion: when FRED publishes a new value for a tracked series, the
scheduled sync stores it with its as-of date within 24 hours. So the job runs daily
and keys on `(series, date)` — the *observation* date, not the fetch date. FRED
revises figures after first publication, and an upsert on that key means a revision
replaces the earlier number in place rather than leaving M3 to choose between two
rows for one month.
"""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.logging import get_logger, safe_extra
from app.models.economic_indicator import EconomicIndicator
from app.models.enums import EconomicSeries, IngestionStatus
from app.models.ingestion_run import ECONOMIC_JOB, IngestionRun
from app.providers.base import EconomicDataProvider, ProviderError, SeriesPoint
from app.providers.retry import with_retry
from app.services.ingestion.runs import finish_run, start_run

logger = get_logger(__name__)

# Macro series are revised for months after first release, and GDP is quarterly, so
# a short incremental window would keep re-fetching the same handful of points and
# still miss revisions. A year is cheap — five requests — and catches them all.
#
# It is *not* enough for a first load, though: the M3 feature pipeline joins these
# onto years of price history, and a twelve-month macro window leaves every older
# row without a value. `--backfill-days` covers that case; see `app.jobs bootstrap`.
DEFAULT_LOOKBACK_DAYS = 365


def upsert_points(
    db: Session, series: EconomicSeries, points: list[SeriesPoint], source: str
) -> int:
    if not points:
        return 0

    rows = [
        {
            "series": series,
            "date": point.date,
            "value": point.value,
            "source": source,
            "ingested_at": datetime.now(UTC),
        }
        for point in points
    ]

    statement = insert(EconomicIndicator).values(rows)
    statement = statement.on_conflict_do_update(
        constraint="uq_economic_indicator_series_date",
        set_={
            "value": statement.excluded.value,
            "source": statement.excluded.source,
            "ingested_at": statement.excluded.ingested_at,
        },
    )

    # RETURNING, not `rowcount` — see the note in `market.upsert_bars`.
    written = len(db.execute(statement.returning(EconomicIndicator.id)).all())
    db.commit()
    return written


def ingest_economic_data(
    db: Session,
    provider: EconomicDataProvider,
    *,
    series: list[EconomicSeries] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> IngestionRun:
    end = end or datetime.now(UTC).date()
    start = start or end - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    tracked = series or list(EconomicSeries)

    run = start_run(db, ECONOMIC_JOB)

    written = 0
    ok: list[str] = []
    failures: list[str] = []
    per_series: dict[str, int] = {}

    for item in tracked:
        # Bound as a default argument so each iteration captures its own series
        # rather than closing over the loop variable.
        def fetch(series_item: EconomicSeries = item) -> list[SeriesPoint]:
            return provider.fetch_series(series_item, start, end)

        try:
            points = with_retry(fetch, description=f"fetch_series({item})")
        except ProviderError as exc:
            logger.error(
                "ingest_series_failed",
                extra=safe_extra(
                    series=str(item), provider=provider.name, error_type=type(exc).__name__
                ),
            )
            failures.append(f"{item}: {type(exc).__name__}: {exc}")
            continue

        count = upsert_points(db, item, points, provider.name)
        written += count
        per_series[str(item)] = len(points)
        ok.append(str(item))

    if not ok:
        status = IngestionStatus.FAILED
    elif failures:
        status = IngestionStatus.PARTIAL
    else:
        status = IngestionStatus.SUCCESS

    return finish_run(
        db,
        run,
        status=status,
        rows_written=written,
        symbols_ok=len(ok),
        symbols_failed=len(failures),
        quality={
            "provider": provider.name,
            "observations_by_series": per_series,
            "window": {"start": start.isoformat(), "end": end.isoformat()},
        },
        error="; ".join(failures[:10]) or None,
    )

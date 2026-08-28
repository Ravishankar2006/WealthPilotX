"""Market data ingestion (FR-04).

The job's contract, straight from the acceptance criteria:

1. After a run, every tracked symbol has a row for the latest trading day — *or* a
   logged, alertable failure. Silence is not an allowed outcome.
2. A provider outage retries with backoff and surfaces a health-check alert rather
   than skipping the day.

Everything here is built around being safely re-runnable, because "run it again" is
the entire recovery story for a partial failure. Writes are upserts keyed on
`(asset_id, date)`, and each symbol resumes from its own latest stored date.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger, safe_extra
from app.models.asset import Asset
from app.models.enums import IngestionStatus
from app.models.ingestion_run import MARKET_JOB, IngestionRun
from app.models.market_data import MarketData
from app.providers.base import (
    MarketDataProvider,
    PriceBar,
    ProviderError,
    SymbolNotFoundError,
)
from app.providers.retry import with_retry
from app.services.ingestion.cleaning import QualityReport, clean_bars
from app.services.ingestion.runs import finish_run, start_run

logger = get_logger(__name__)

# Re-fetch a few days either side of the last stored bar. Providers correct recent
# sessions after the fact, and the upsert makes the overlap free.
RESYNC_OVERLAP_DAYS = 5


@dataclass(slots=True)
class SymbolOutcome:
    symbol: str
    rows_written: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def latest_stored_date(db: Session, asset_id: object) -> date | None:
    return db.scalar(select(func.max(MarketData.date)).where(MarketData.asset_id == asset_id))


def tracked_assets(db: Session, symbols: list[str] | None = None) -> list[Asset]:
    statement = select(Asset).order_by(Asset.symbol)
    if symbols:
        statement = statement.where(Asset.symbol.in_([s.upper() for s in symbols]))
    else:
        # An inactive asset keeps its history but stops consuming provider calls.
        statement = statement.where(Asset.is_active.is_(True))
    return list(db.scalars(statement))


def upsert_bars(db: Session, asset_id: object, bars: list[PriceBar], source: str) -> int:
    """Insert or update bars, returning the number of rows touched.

    `ON CONFLICT (asset_id, date) DO UPDATE` is what makes the job idempotent: a
    re-run overwrites a provisional bar with its corrected version instead of either
    duplicating it or requiring a read-modify-write per row.
    """
    if not bars:
        return 0

    rows = [
        {
            "asset_id": asset_id,
            "date": bar.date,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "adj_close": bar.adj_close,
            "volume": bar.volume,
            "source": source,
            "ingested_at": datetime.now(UTC),
        }
        for bar in bars
    ]

    statement = insert(MarketData).values(rows)
    statement = statement.on_conflict_do_update(
        constraint="uq_market_data_asset_date",
        set_={
            "open": statement.excluded.open,
            "high": statement.excluded.high,
            "low": statement.excluded.low,
            "close": statement.excluded.close,
            "adj_close": statement.excluded.adj_close,
            "volume": statement.excluded.volume,
            "source": statement.excluded.source,
            "ingested_at": statement.excluded.ingested_at,
        },
    )

    # Counted via RETURNING rather than `rowcount`, which psycopg reports as -1 for
    # a multi-row INSERT. Taking that at face value made a successful run report a
    # negative row count — wrong in the direction that looks like nothing happened.
    written = len(db.execute(statement.returning(MarketData.id)).all())
    db.commit()
    return written


def ingest_symbol(
    db: Session,
    asset: Asset,
    provider: MarketDataProvider,
    *,
    start: date | None,
    end: date,
    outlier_threshold: float,
) -> tuple[SymbolOutcome, QualityReport]:
    """Fetch, clean and store one symbol. Provider failures are captured, not raised.

    Returning the failure rather than raising it is deliberate: one dead ticker must
    not abandon the other 31 symbols, and the run-level status still records that
    something went wrong.
    """
    window_start = start
    if window_start is None:
        stored = latest_stored_date(db, asset.id)
        window_start = (
            stored - timedelta(days=RESYNC_OVERLAP_DAYS)
            if stored
            else end - timedelta(days=get_settings().ingestion_backfill_days)
        )

    try:
        bars = with_retry(
            lambda: provider.fetch_daily_bars(asset.symbol, window_start, end),
            description=f"fetch_daily_bars({asset.symbol})",
        )
    except SymbolNotFoundError as exc:
        # Permanent for this symbol. Still a failure for the run — a silently
        # missing asset is exactly what FR-04 is written to prevent — but it is
        # not worth retrying on the next schedule either, so it is logged plainly.
        logger.warning(
            "ingest_symbol_not_found",
            extra=safe_extra(symbol=asset.symbol, provider=provider.name),
        )
        return SymbolOutcome(asset.symbol, error=f"{type(exc).__name__}: {exc}"), QualityReport(
            symbol=asset.symbol
        )
    except ProviderError as exc:
        logger.error(
            "ingest_symbol_failed",
            extra=safe_extra(
                symbol=asset.symbol, provider=provider.name, error_type=type(exc).__name__
            ),
        )
        return SymbolOutcome(asset.symbol, error=f"{type(exc).__name__}: {exc}"), QualityReport(
            symbol=asset.symbol
        )

    cleaned, report = clean_bars(asset.symbol, bars, outlier_threshold=outlier_threshold)
    written = upsert_bars(db, asset.id, cleaned, provider.name)

    logger.info(
        "ingest_symbol_complete",
        extra=safe_extra(symbol=asset.symbol, quality=report.as_dict(), rows_written=written),
    )
    return SymbolOutcome(asset.symbol, rows_written=written), report


def ingest_market_data(
    db: Session,
    provider: MarketDataProvider,
    *,
    symbols: list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> IngestionRun:
    """Run the full market ingestion and record its outcome.

    Status semantics (Phase 2 plan, decision 6):
      SUCCESS — every symbol fetched.
      PARTIAL — some fetched, some failed. Distinct from SUCCESS on purpose: the
                gap it leaves is invisible to M3 unless the run says so.
      FAILED  — nothing fetched, or there was nothing to fetch.
    """
    settings = get_settings()
    end = end or datetime.now(UTC).date()
    run = start_run(db, MARKET_JOB)

    assets = tracked_assets(db, symbols)
    if not assets:
        return finish_run(
            db,
            run,
            status=IngestionStatus.FAILED,
            error="No tracked assets. Run `python -m app.jobs seed-assets` first.",
        )

    totals = QualityReport()
    outcomes: list[SymbolOutcome] = []

    for asset in assets:
        outcome, report = ingest_symbol(
            db,
            asset,
            provider,
            start=start,
            end=end,
            outlier_threshold=settings.outlier_log_return_threshold,
        )
        outcomes.append(outcome)
        totals.merge(report)

    failed = [outcome for outcome in outcomes if not outcome.ok]
    succeeded = [outcome for outcome in outcomes if outcome.ok]

    if not succeeded:
        status = IngestionStatus.FAILED
    elif failed:
        status = IngestionStatus.PARTIAL
    else:
        status = IngestionStatus.SUCCESS

    quality = totals.as_dict()
    quality["provider"] = provider.name
    quality["window"] = {
        "start": start.isoformat() if start else "incremental",
        "end": end.isoformat(),
    }

    return finish_run(
        db,
        run,
        status=status,
        rows_written=sum(outcome.rows_written for outcome in outcomes),
        symbols_ok=len(succeeded),
        symbols_failed=len(failed),
        quality=quality,
        # Names and error types only — bounded, and free of anything a provider
        # payload might have carried.
        error="; ".join(f"{o.symbol}: {o.error}" for o in failed[:10]) or None,
    )

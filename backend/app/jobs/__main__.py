"""Job CLI — `python -m app.jobs <command>`.

Commands:
    seed-assets       Insert or refresh the tracked asset universe.
    ingest-market     FR-04. Incremental by default; --backfill-days for history.
    ingest-economic   FR-05. Fetches the five tracked FRED series.
    bootstrap         Seed, then backfill *only if* no market data exists yet.
    train-risk        FR-03. Train the risk classifier on the rubric population.
    train-prediction  FR-08. Train the market predictor on stored history.
    predict           FR-08. Write a prediction row per tracked asset.
    promote           §10.5. Promote a model, refusing a regression.
    models            List registered models and their promotion metric.

Exit codes are meaningful, because a scheduler and a CI step both read them:
    0  SUCCESS
    1  FAILED — nothing was ingested
    2  PARTIAL — some symbols or series failed (FR-04: never a silent skip)
"""

import argparse
import sys
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging, correlation_id, get_logger, safe_extra
from app.db.session import SessionLocal
from app.models.enums import EconomicSeries, IngestionStatus
from app.models.ingestion_run import IngestionRun
from app.models.market_data import MarketData
from app.providers.registry import get_economic_provider, get_market_provider
from app.services.ingestion.economic import ingest_economic_data
from app.services.ingestion.market import ingest_market_data
from app.services.ingestion.runs import run_summary
from app.services.ingestion.seed import seed_assets

logger = get_logger("app.jobs")

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_PARTIAL = 2

_EXIT_FOR_STATUS = {
    IngestionStatus.SUCCESS: EXIT_OK,
    IngestionStatus.PARTIAL: EXIT_PARTIAL,
    IngestionStatus.FAILED: EXIT_FAILED,
    IngestionStatus.RUNNING: EXIT_FAILED,
}


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not an ISO date (YYYY-MM-DD).") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.jobs", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("seed-assets", help="Insert or refresh the tracked asset universe.")

    commands.add_parser(
        "bootstrap",
        help="Seed the universe, then backfill history only if the database is empty.",
    )

    market = commands.add_parser("ingest-market", help="Ingest daily OHLCV (FR-04).")
    market.add_argument(
        "--symbols",
        nargs="+",
        help="Limit to these symbols. Defaults to every active tracked asset.",
    )
    market.add_argument(
        "--backfill-days",
        type=int,
        help="Fetch this many days of history instead of resuming from the last stored bar.",
    )
    market.add_argument("--start", type=_parse_date, help="Explicit window start (YYYY-MM-DD).")
    market.add_argument("--end", type=_parse_date, help="Explicit window end (YYYY-MM-DD).")
    market.add_argument("--provider", help="Override the configured market provider.")

    economic = commands.add_parser("ingest-economic", help="Ingest FRED series (FR-05).")
    economic.add_argument(
        "--series",
        nargs="+",
        choices=[str(series) for series in EconomicSeries],
        help="Limit to these series. Defaults to all five.",
    )
    economic.add_argument(
        "--backfill-days",
        type=int,
        help="Fetch this many days of history instead of the default revision window. "
        "Needed for a first load: the M3 feature pipeline joins these onto years of "
        "price history, and the default 365-day window leaves older rows uncovered.",
    )
    economic.add_argument("--start", type=_parse_date, help="Explicit window start (YYYY-MM-DD).")
    economic.add_argument("--end", type=_parse_date, help="Explicit window end (YYYY-MM-DD).")
    economic.add_argument("--provider", help="Override the configured economic provider.")

    commands.add_parser("train-risk", help="Train the FR-03 risk classifier.")
    commands.add_parser("train-prediction", help="Train the FR-08 market predictor.")
    commands.add_parser("predict", help="Generate predictions for every tracked asset.")

    promote = commands.add_parser("promote", help="Promote a model to PRODUCTION (§10.5).")
    promote.add_argument("name", help="Model name, e.g. risk_classifier.")
    promote.add_argument("version", help="Version to promote, e.g. v1.")
    promote.add_argument(
        "--force",
        action="store_true",
        help="Promote despite losing to the incumbent. For a genuinely incomparable "
        "metric only — a changed target or feature set. Logged loudly.",
    )

    models = commands.add_parser("models", help="List registered models.")
    models.add_argument("--name", help="Limit to one model name.")

    return parser


def _report(run: IngestionRun) -> int:
    summary = run_summary(run)
    logger.info("job_finished", extra=safe_extra(**summary))
    # stdout as well as the log: an operator running this by hand should not have to
    # read JSON to find out what happened.
    print(  # noqa: T201 — this is a CLI
        f"{summary['job']}: {summary['status']} — {summary['rows_written']} rows, "
        f"{summary['symbols_ok']} ok, {summary['symbols_failed']} failed"
    )
    return _EXIT_FOR_STATUS[run.status]


def _bootstrap(db: Session, backfill_days: int) -> int:
    """First-boot data load.

    Without this a fresh stack has an empty `market_data` table until the first
    scheduled run, which can be most of a day away — so "one command to a working
    app" would be true of the API and false of the data.

    Guarded on the table being empty rather than run unconditionally: the container
    restarts, and 32 provider calls on every restart is exactly the pattern that
    gets an unofficial API to rate-limit you (§7.3).
    """
    seed_assets(db)

    if db.scalar(select(func.count()).select_from(MarketData)):
        logger.info("bootstrap_skipped_market_data_present")
        print("bootstrap: assets seeded; market data already present, skipping backfill")  # noqa: T201
        return EXIT_OK

    end = datetime.now(UTC).date()
    market = ingest_market_data(
        db,
        get_market_provider(),
        start=end - timedelta(days=backfill_days),
        end=end,
    )
    # Same window as the market backfill: macro data that starts years after the
    # price history leaves the older rows without a value.
    economic = ingest_economic_data(
        db,
        get_economic_provider(),
        start=end - timedelta(days=backfill_days),
        end=end,
    )

    for run in (market, economic):
        _report(run)

    # The worse of the two outcomes, so a failed backfill is visible to whatever
    # started the container rather than hidden behind the other job's success.
    return max(_EXIT_FOR_STATUS[market.status], _EXIT_FOR_STATUS[economic.status])


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    settings = get_settings()
    configure_logging("DEBUG" if settings.debug else "INFO")
    # Jobs get a correlation ID too, so a run's log lines can be pulled together the
    # same way a request's can (§16.4). It is stored on the ingestion_runs row.
    correlation_id.set(str(uuid.uuid4()))

    db = SessionLocal()
    try:
        return _dispatch(args, db, settings)
    except AppError as exc:
        # AppError carries a message written for a human — a refused promotion, an
        # unknown model version. Printing that and exiting non-zero is the useful
        # behaviour; a traceback buries the message and implies the tool broke
        # rather than that it did its job and said no.
        print(f"error: {exc.message}", file=sys.stderr)  # noqa: T201
        return EXIT_FAILED
    finally:
        db.close()


def _dispatch(args: argparse.Namespace, db: Session, settings: Settings) -> int:
    if args.command == "seed-assets":
        created, updated = seed_assets(db)
        print(f"seed-assets: {created} created, {updated} updated")  # noqa: T201
        return EXIT_OK

    if args.command == "bootstrap":
        return _bootstrap(db, settings.ingestion_backfill_days)

    if args.command == "ingest-market":
        start = args.start
        if start is None and args.backfill_days:
            end = args.end or datetime.now(UTC).date()
            start = end - timedelta(days=args.backfill_days)
        return _report(
            ingest_market_data(
                db,
                get_market_provider(args.provider),
                symbols=args.symbols,
                start=start,
                end=args.end,
            )
        )

    if args.command == "ingest-economic":
        start = args.start
        if start is None and args.backfill_days:
            end = args.end or datetime.now(UTC).date()
            start = end - timedelta(days=args.backfill_days)
        return _report(
            ingest_economic_data(
                db,
                get_economic_provider(args.provider),
                series=[EconomicSeries(s) for s in args.series] if args.series else None,
                start=start,
                end=args.end,
            )
        )

    # Imported lazily: these pull in the ML stack, and `seed-assets` has no business
    # paying that import cost just to parse its arguments.
    from app.jobs import ml

    if args.command == "train-risk":
        ml.train_risk(db)
        return EXIT_OK

    if args.command == "train-prediction":
        ml.train_prediction(db)
        return EXIT_OK

    if args.command == "predict":
        return EXIT_OK if ml.generate_predictions(db) else EXIT_FAILED

    if args.command == "promote":
        ml.promote(db, args.name, args.version, force=args.force)
        return EXIT_OK

    if args.command == "models":
        ml.list_models(db, args.name)
        return EXIT_OK

    return EXIT_FAILED  # unreachable: argparse rejects an unknown command


if __name__ == "__main__":
    sys.exit(main())

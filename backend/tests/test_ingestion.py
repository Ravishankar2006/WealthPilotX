"""FR-04 and FR-05 acceptance criteria, against a real database.

The two criteria that shape this file:

* FR-04: after a run, each tracked symbol has a row for the latest trading day *or*
  a logged, alertable failure — never a silent skip.
* FR-05: a newly published value is stored with its as-of date.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.data.asset_universe import UNIVERSE
from app.models.asset import Asset
from app.models.economic_indicator import EconomicIndicator
from app.models.enums import AssetClass, EconomicSeries, IngestionStatus
from app.models.ingestion_run import MARKET_JOB, IngestionRun
from app.models.market_data import MarketData
from app.providers.base import (
    PriceBar,
    ProviderUnavailableError,
    SeriesPoint,
    SymbolNotFoundError,
)
from app.services.ingestion.economic import ingest_economic_data
from app.services.ingestion.market import ingest_market_data
from app.services.ingestion.runs import ingestion_health, job_health
from app.services.ingestion.seed import seed_assets

END = date(2026, 6, 1)
START = date(2026, 1, 1)


class StubMarketProvider:
    """A provider whose behaviour per symbol is dictated by the test."""

    name = "stub"

    def __init__(
        self, bars: dict[str, list[PriceBar]] | None = None, fail: Exception | None = None
    ):
        self._bars = bars or {}
        self._fail = fail
        self.calls: list[str] = []

    def fetch_daily_bars(self, symbol: str, start: date, end: date) -> list[PriceBar]:
        self.calls.append(symbol)
        if self._fail is not None:
            raise self._fail
        return self._bars.get(symbol, [])


class StubEconomicProvider:
    name = "stub"

    def __init__(self, points: list[SeriesPoint] | None = None, fail: Exception | None = None):
        self._points = points or []
        self._fail = fail

    def fetch_series(self, series: EconomicSeries, start: date, end: date) -> list[SeriesPoint]:
        if self._fail is not None:
            raise self._fail
        return self._points


def make_bars(count: int, start: date = START, close: str = "100.00") -> list[PriceBar]:
    from decimal import Decimal

    price = Decimal(close)
    return [
        PriceBar(
            date=start + timedelta(days=n),
            open=price,
            high=price + Decimal("1"),
            low=price - Decimal("1"),
            close=price,
            adj_close=price,
            volume=1_000 + n,
        )
        for n in range(count)
    ]


@pytest.fixture
def one_asset(db: Session) -> Asset:
    asset = Asset(
        symbol="TEST",
        name="Test Instrument",
        asset_type="ETF",  # type: ignore[arg-type]
        asset_class=AssetClass.EQUITY,
        currency="USD",
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


class TestSeeding:
    def test_it_creates_the_whole_universe(self, db: Session) -> None:
        created, updated = seed_assets(db)
        assert created == len(UNIVERSE)
        assert updated == 0
        assert db.scalar(select(func.count()).select_from(Asset)) == len(UNIVERSE)

    def test_it_is_idempotent(self, db: Session) -> None:
        seed_assets(db)
        created, updated = seed_assets(db)
        assert (created, updated) == (0, 0)
        assert db.scalar(select(func.count()).select_from(Asset)) == len(UNIVERSE)

    def test_it_refreshes_changed_metadata_in_place(self, db: Session) -> None:
        seed_assets(db)
        asset = db.scalar(select(Asset).where(Asset.symbol == "SPY"))
        assert asset is not None
        asset.name = "stale name"
        asset.is_active = False
        db.commit()

        created, updated = seed_assets(db)
        db.refresh(asset)
        assert (created, updated) == (0, 1)
        # Reactivated rather than duplicated — a second row would orphan the history.
        assert asset.is_active is True
        assert asset.name != "stale name"

    def test_every_asset_carries_a_class_for_fr_11(self, db: Session) -> None:
        """FR-11 caps weights by asset class; a null class breaks the optimizer."""
        seed_assets(db)
        assert all(asset.asset_class is not None for asset in db.scalars(select(Asset)))


class TestMarketIngestion:
    def test_it_stores_bars_and_records_success(self, db: Session, one_asset: Asset) -> None:
        provider = StubMarketProvider({"TEST": make_bars(10)})
        run = ingest_market_data(db, provider, end=END)

        assert run.status is IngestionStatus.SUCCESS
        assert run.symbols_ok == 1 and run.symbols_failed == 0
        assert db.scalar(select(func.count()).select_from(MarketData)) == 10
        # Regression: psycopg reports rowcount -1 for a multi-row INSERT, so counting
        # that way made a successful run report -1 rows per symbol — wrong in the
        # direction that looks like nothing happened.
        assert run.rows_written == 10

    def test_rerunning_writes_no_duplicates(self, db: Session, one_asset: Asset) -> None:
        """FR-06: zero duplicate (asset_id, date) rows. Also the whole recovery story
        for a partial failure — "just run it again" has to be safe."""
        provider = StubMarketProvider({"TEST": make_bars(10)})
        ingest_market_data(db, provider, end=END)
        ingest_market_data(db, provider, end=END)

        assert db.scalar(select(func.count()).select_from(MarketData)) == 10

    def test_a_rerun_updates_a_corrected_bar_in_place(self, db: Session, one_asset: Asset) -> None:
        from decimal import Decimal

        ingest_market_data(db, StubMarketProvider({"TEST": make_bars(1, close="100.00")}), end=END)
        ingest_market_data(db, StubMarketProvider({"TEST": make_bars(1, close="123.45")}), end=END)

        rows = list(db.scalars(select(MarketData)))
        assert len(rows) == 1
        assert rows[0].close == Decimal("123.450000")

    def test_a_provider_outage_fails_the_run_rather_than_skipping_the_day(
        self, db: Session, one_asset: Asset
    ) -> None:
        """FR-04 acceptance criterion 2, the one that matters most: an outage must be
        loud. A run that quietly wrote nothing looks identical to a clean day."""
        run = ingest_market_data(
            db, StubMarketProvider(fail=ProviderUnavailableError("network down")), end=END
        )

        assert run.status is IngestionStatus.FAILED
        assert run.symbols_failed == 1
        assert run.error is not None and "ProviderUnavailableError" in run.error
        assert db.scalar(select(func.count()).select_from(MarketData)) == 0

    def test_an_outage_is_retried_before_the_run_is_failed(
        self, db: Session, one_asset: Asset, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.providers.retry.time.sleep", lambda _: None)
        provider = StubMarketProvider(fail=ProviderUnavailableError("network down"))
        ingest_market_data(db, provider, end=END)
        assert len(provider.calls) > 1

    def test_one_dead_symbol_does_not_abandon_the_rest(self, db: Session) -> None:
        db.add_all(
            [
                Asset(symbol="GOOD", asset_type="ETF", asset_class=AssetClass.EQUITY),  # type: ignore[arg-type]
                Asset(symbol="DEAD", asset_type="ETF", asset_class=AssetClass.EQUITY),  # type: ignore[arg-type]
            ]
        )
        db.commit()

        class Mixed(StubMarketProvider):
            def fetch_daily_bars(self, symbol: str, start: date, end: date) -> list[PriceBar]:
                if symbol == "DEAD":
                    raise SymbolNotFoundError("delisted")
                return make_bars(5)

        run = ingest_market_data(db, Mixed(), end=END)

        # PARTIAL, not SUCCESS: the gap DEAD leaves is invisible to M3 unless the
        # run says so.
        assert run.status is IngestionStatus.PARTIAL
        assert (run.symbols_ok, run.symbols_failed) == (1, 1)
        assert db.scalar(select(func.count()).select_from(MarketData)) == 5

    def test_a_delisted_symbol_is_not_retried(
        self, db: Session, one_asset: Asset, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.providers.retry.time.sleep", lambda _: None)
        provider = StubMarketProvider(fail=SymbolNotFoundError("delisted"))
        ingest_market_data(db, provider, end=END)
        assert len(provider.calls) == 1

    def test_it_fails_loudly_when_no_assets_are_seeded(self, db: Session) -> None:
        run = ingest_market_data(db, StubMarketProvider(), end=END)
        assert run.status is IngestionStatus.FAILED
        assert run.error is not None and "seed-assets" in run.error

    def test_the_quality_report_is_persisted_for_audit(self, db: Session, one_asset: Asset) -> None:
        """FR-06: the report must be available for audit, not only in stdout."""
        run = ingest_market_data(db, StubMarketProvider({"TEST": make_bars(6)}), end=END)
        assert run.quality is not None
        assert run.quality["rows_in"] == 6
        assert run.quality["rows_out"] == 6
        assert run.quality["provider"] == "stub"
        assert "null_rate" in run.quality

    def test_structurally_invalid_rows_never_reach_the_database(
        self, db: Session, one_asset: Asset
    ) -> None:
        from decimal import Decimal

        good = make_bars(2)
        broken = PriceBar(
            date=date(2026, 3, 1),
            open=Decimal("10"),
            high=Decimal("5"),
            low=Decimal("50"),
            close=Decimal("10"),
            adj_close=Decimal("10"),
            volume=1,
        )
        run = ingest_market_data(db, StubMarketProvider({"TEST": [*good, broken]}), end=END)
        assert db.scalar(select(func.count()).select_from(MarketData)) == 2
        assert run.quality is not None
        assert run.quality["inverted_range_rejected"] == 1

    def test_only_active_assets_are_fetched(self, db: Session, one_asset: Asset) -> None:
        one_asset.is_active = False
        db.commit()
        provider = StubMarketProvider({"TEST": make_bars(3)})
        ingest_market_data(db, provider, end=END)
        assert provider.calls == []

    def test_the_symbols_flag_narrows_the_run(self, db: Session) -> None:
        db.add_all(
            [
                Asset(symbol="AAA", asset_type="ETF", asset_class=AssetClass.EQUITY),  # type: ignore[arg-type]
                Asset(symbol="BBB", asset_type="ETF", asset_class=AssetClass.EQUITY),  # type: ignore[arg-type]
            ]
        )
        db.commit()
        provider = StubMarketProvider({"AAA": make_bars(2)})
        ingest_market_data(db, provider, symbols=["aaa"], end=END)
        assert provider.calls == ["AAA"]


class TestEconomicIngestion:
    def test_it_stores_observations_with_their_as_of_dates(self, db: Session) -> None:
        """FR-05 acceptance criterion: the value is stored with its as-of date."""
        from decimal import Decimal

        points = [
            SeriesPoint(date=date(2026, 1, 1), value=Decimal("3.1")),
            SeriesPoint(date=date(2026, 2, 1), value=Decimal("3.2")),
        ]
        run = ingest_economic_data(
            db, StubEconomicProvider(points), series=[EconomicSeries.INFLATION], end=END
        )

        assert run.status is IngestionStatus.SUCCESS
        assert run.rows_written == 2
        rows = list(db.scalars(select(EconomicIndicator).order_by(EconomicIndicator.date)))
        assert [row.date for row in rows] == [date(2026, 1, 1), date(2026, 2, 1)]
        assert rows[0].value == Decimal("3.100000")

    def test_a_revision_overwrites_in_place(self, db: Session) -> None:
        """FRED revises published figures. Two rows for one as-of date would leave M3
        to choose between them, and it would choose silently."""
        from decimal import Decimal

        first = [SeriesPoint(date=date(2026, 1, 1), value=Decimal("3.1"))]
        revised = [SeriesPoint(date=date(2026, 1, 1), value=Decimal("3.4"))]

        ingest_economic_data(db, StubEconomicProvider(first), series=[EconomicSeries.GDP], end=END)
        ingest_economic_data(
            db, StubEconomicProvider(revised), series=[EconomicSeries.GDP], end=END
        )

        rows = list(db.scalars(select(EconomicIndicator)))
        assert len(rows) == 1
        assert rows[0].value == Decimal("3.400000")

    def test_all_five_series_are_ingested_by_default(
        self, db: Session, economic_provider: object
    ) -> None:
        run = ingest_economic_data(db, economic_provider, end=END)  # type: ignore[arg-type]
        assert run.status is IngestionStatus.SUCCESS
        assert run.symbols_ok == len(EconomicSeries)
        stored = set(db.scalars(select(EconomicIndicator.series).distinct()))
        assert stored == set(EconomicSeries)

    def test_a_provider_outage_fails_the_run(
        self, db: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.providers.retry.time.sleep", lambda _: None)
        run = ingest_economic_data(
            db, StubEconomicProvider(fail=ProviderUnavailableError("down")), end=END
        )
        assert run.status is IngestionStatus.FAILED
        assert run.symbols_failed == len(EconomicSeries)


class TestIngestionHealth:
    def test_a_job_that_never_ran_is_not_an_alert(self, db: Session) -> None:
        """Day one is not a failure, and an alert that fires on every fresh install
        is an alert nobody reads."""
        health = job_health(db, MARKET_JOB, stale_after_hours=48)
        assert health.last_status == "never_run"
        assert health.healthy is True

    def test_a_successful_run_reports_healthy(self, db: Session, one_asset: Asset) -> None:
        ingest_market_data(db, StubMarketProvider({"TEST": make_bars(3)}), end=END)
        assert job_health(db, MARKET_JOB, stale_after_hours=48).healthy is True

    def test_a_failed_run_reports_unhealthy(self, db: Session, one_asset: Asset) -> None:
        ingest_market_data(db, StubMarketProvider(fail=ProviderUnavailableError("x")), end=END)
        health = job_health(db, MARKET_JOB, stale_after_hours=48)
        assert health.healthy is False
        assert health.last_status == str(IngestionStatus.FAILED)

    def test_an_old_success_goes_stale(self, db: Session, one_asset: Asset) -> None:
        run = ingest_market_data(db, StubMarketProvider({"TEST": make_bars(3)}), end=END)
        stored = db.get(IngestionRun, run.id)
        assert stored is not None
        stored.started_at = datetime.now(UTC) - timedelta(days=10)
        db.commit()

        health = job_health(db, MARKET_JOB, stale_after_hours=48)
        assert health.stale is True
        assert health.healthy is False

    def test_the_health_summary_covers_both_jobs(self, db: Session) -> None:
        summary = ingestion_health(db, stale_after_hours=48)
        assert {job["job"] for job in summary["jobs"]} == {"ingest_market", "ingest_economic"}
        assert summary["latest_market_date"] is None


class TestBootstrap:
    """First-boot data load. The guard matters more than the load: an unconditional
    backfill would re-run on every container restart, which is exactly how you get
    an unofficial API to rate-limit you (§7.3)."""

    def test_it_seeds_and_backfills_an_empty_database(self, db: Session) -> None:
        from app.jobs.__main__ import _bootstrap

        assert _bootstrap(db, backfill_days=30) == 0
        assert db.scalar(select(func.count()).select_from(Asset)) == len(UNIVERSE)
        assert db.scalar(select(func.count()).select_from(MarketData)) > 0
        assert db.scalar(select(func.count()).select_from(EconomicIndicator)) > 0

    def test_it_skips_the_backfill_when_data_already_exists(
        self, db: Session, one_asset: Asset
    ) -> None:
        from app.jobs.__main__ import _bootstrap

        ingest_market_data(db, StubMarketProvider({"TEST": make_bars(3)}), end=END)
        before = db.scalar(select(func.count()).select_from(IngestionRun))

        assert _bootstrap(db, backfill_days=30) == 0
        # No new ingestion run at all — the guard short-circuits before the provider.
        assert db.scalar(select(func.count()).select_from(IngestionRun)) == before

    def test_it_still_seeds_when_it_skips_the_backfill(self, db: Session, one_asset: Asset) -> None:
        from app.jobs.__main__ import _bootstrap

        ingest_market_data(db, StubMarketProvider({"TEST": make_bars(3)}), end=END)
        _bootstrap(db, backfill_days=30)
        # The seed still runs: a new symbol added to the universe must appear even on
        # a database that already holds price history.
        assert db.scalar(select(func.count()).select_from(Asset)) == len(UNIVERSE) + 1

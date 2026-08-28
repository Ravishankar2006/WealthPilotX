"""The §7.3 provider seam: retry policy, and payload parsing per vendor.

These are unit tests with no network. That is the point of the abstraction — the
suite must not go red because Yahoo had a bad afternoon.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.enums import EconomicSeries
from app.providers.base import (
    EconomicDataProvider,
    MarketDataProvider,
    ProviderConfigurationError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
    SymbolNotFoundError,
)
from app.providers.fred import SERIES_IDS, FredEconomicDataProvider, parse_observations
from app.providers.registry import get_economic_provider, get_market_provider
from app.providers.retry import backoff_delay, with_retry
from app.providers.synthetic import SyntheticEconomicDataProvider, SyntheticMarketDataProvider


class TestRetry:
    def test_succeeds_without_sleeping_when_the_call_works(self) -> None:
        slept: list[float] = []
        assert with_retry(lambda: "ok", description="x", sleep=slept.append) == "ok"
        assert slept == []

    def test_retries_a_transient_failure_then_succeeds(self) -> None:
        attempts = {"n": 0}
        slept: list[float] = []

        def flaky() -> str:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise ProviderUnavailableError("down")
            return "ok"

        result = with_retry(flaky, description="x", sleep=slept.append, jitter=lambda: 1.0)
        assert result == "ok"
        assert attempts["n"] == 3
        assert len(slept) == 2

    def test_gives_up_after_the_attempt_budget_and_reraises(self) -> None:
        attempts = {"n": 0}

        def always_down() -> str:
            attempts["n"] += 1
            raise ProviderUnavailableError("down")

        with pytest.raises(ProviderUnavailableError):
            with_retry(always_down, description="x", attempts=3, sleep=lambda _: None)
        assert attempts["n"] == 3

    @pytest.mark.parametrize(
        "error",
        [SymbolNotFoundError("gone"), ProviderConfigurationError("no key")],
    )
    def test_permanent_failures_are_not_retried(self, error: Exception) -> None:
        """Retrying a delisted ticker or a missing API key only delays the run."""
        attempts = {"n": 0}

        def fails() -> str:
            attempts["n"] += 1
            raise error

        with pytest.raises(type(error)):
            with_retry(fails, description="x", sleep=lambda _: None)
        assert attempts["n"] == 1

    def test_backoff_grows_exponentially_and_is_capped(self) -> None:
        delays = [backoff_delay(n, base=1.0, maximum=10.0, jitter=lambda: 1.0) for n in range(1, 6)]
        assert delays == [1.0, 2.0, 4.0, 8.0, 10.0]

    def test_jitter_is_applied(self) -> None:
        assert backoff_delay(3, base=1.0, maximum=100.0, jitter=lambda: 0.5) == 2.0

    def test_a_rate_limit_backs_off_harder_than_an_outage(self) -> None:
        """The provider is telling us our pace is the problem — pausing for the same
        second we would use for a dropped connection just re-triggers it."""
        outage: list[float] = []
        limited: list[float] = []

        for error, sink in (
            (ProviderUnavailableError, outage),
            (ProviderRateLimitedError, limited),
        ):

            def fail(error: type[Exception] = error) -> str:
                raise error("nope")

            with pytest.raises(error):
                with_retry(fail, description="x", attempts=2, sleep=sink.append, jitter=lambda: 1.0)

        assert limited[0] > outage[0]


class TestFredParsing:
    def test_parses_observations_into_decimals(self) -> None:
        points = parse_observations(
            {
                "observations": [
                    {"date": "2026-01-01", "value": "3.5"},
                    {"date": "2026-02-01", "value": "3.6"},
                ]
            }
        )
        assert [p.date for p in points] == [date(2026, 1, 1), date(2026, 2, 1)]
        assert points[0].value == Decimal("3.5")

    def test_missing_observations_are_skipped_not_zeroed(self) -> None:
        """FRED writes "." for a value that does not exist. Storing it as 0 would put
        a fabricated data point in front of an M3 model."""
        points = parse_observations(
            {
                "observations": [
                    {"date": "2026-01-01", "value": "."},
                    {"date": "2026-02-01", "value": "3.6"},
                ]
            }
        )
        assert len(points) == 1
        assert points[0].date == date(2026, 2, 1)

    def test_output_is_sorted_by_date(self) -> None:
        points = parse_observations(
            {
                "observations": [
                    {"date": "2026-03-01", "value": "3"},
                    {"date": "2026-01-01", "value": "1"},
                ]
            }
        )
        assert [p.date for p in points] == [date(2026, 1, 1), date(2026, 3, 1)]

    def test_unparseable_rows_do_not_abort_the_batch(self) -> None:
        points = parse_observations(
            {
                "observations": [
                    {"date": "not-a-date", "value": "1"},
                    {"value": "2"},
                    {"date": "2026-01-01", "value": "abc"},
                    {"date": "2026-02-01", "value": "3.6"},
                ]
            }
        )
        assert len(points) == 1

    def test_a_payload_without_observations_is_a_provider_failure(self) -> None:
        with pytest.raises(ProviderUnavailableError):
            parse_observations({"error_message": "Bad Request"})

    def test_every_required_series_has_a_fred_id(self) -> None:
        """FR-05 names five series; a missing mapping would be a KeyError at 3am."""
        assert set(SERIES_IDS) == set(EconomicSeries)

    def test_a_missing_api_key_is_a_configuration_error(self) -> None:
        provider = FredEconomicDataProvider(api_key=None)
        with pytest.raises(ProviderConfigurationError):
            provider.fetch_series(EconomicSeries.GDP, date(2026, 1, 1), date(2026, 2, 1))


class TestSyntheticProviders:
    def test_bars_are_deterministic_for_a_symbol(self) -> None:
        window = (date(2026, 1, 1), date(2026, 3, 1))
        first = SyntheticMarketDataProvider().fetch_daily_bars("SPY", *window)
        second = SyntheticMarketDataProvider().fetch_daily_bars("SPY", *window)
        assert first == second
        assert first != SyntheticMarketDataProvider().fetch_daily_bars("QQQ", *window)

    def test_a_value_does_not_depend_on_the_window_that_asked_for_it(self) -> None:
        """Otherwise an incremental fetch invents a fresh walk and the stored series
        jumps every time the job resumes."""
        wide = SyntheticMarketDataProvider().fetch_daily_bars(
            "SPY", date(2025, 1, 1), date(2026, 3, 1)
        )
        narrow = SyntheticMarketDataProvider().fetch_daily_bars(
            "SPY", date(2026, 1, 1), date(2026, 3, 1)
        )
        overlap = {bar.date: bar for bar in wide}
        assert narrow and all(overlap[bar.date] == bar for bar in narrow)

    def test_bars_satisfy_the_ohlc_invariants(self) -> None:
        bars = SyntheticMarketDataProvider().fetch_daily_bars(
            "SPY", date(2026, 1, 1), date(2026, 6, 1)
        )
        assert bars
        for bar in bars:
            assert bar.high >= bar.low
            assert bar.high >= bar.open and bar.high >= bar.close
            assert bar.low <= bar.open and bar.low <= bar.close
            assert bar.close > 0
            assert bar.volume >= 0

    def test_bars_are_weekdays_only_and_ascending(self) -> None:
        bars = SyntheticMarketDataProvider().fetch_daily_bars(
            "SPY", date(2026, 1, 1), date(2026, 3, 1)
        )
        assert all(bar.date.weekday() < 5 for bar in bars)
        assert [b.date for b in bars] == sorted(b.date for b in bars)

    def test_every_series_produces_plausible_observations(self) -> None:
        provider = SyntheticEconomicDataProvider()
        for series in EconomicSeries:
            points = provider.fetch_series(series, date(2026, 1, 1), date(2026, 12, 1))
            assert points, f"{series} produced nothing"
            assert all(point.value > 0 for point in points)


class TestRegistry:
    def test_returns_the_configured_implementations(self) -> None:
        assert isinstance(get_market_provider(), MarketDataProvider)
        assert isinstance(get_economic_provider(), EconomicDataProvider)

    def test_an_override_selects_a_different_implementation(self) -> None:
        assert get_market_provider("synthetic").name == "synthetic"
        assert get_economic_provider("fred").name == "fred"

    def test_an_unknown_provider_fails_loudly(self) -> None:
        with pytest.raises(ValueError, match="Unknown market data provider"):
            get_market_provider("nasdaq-direct")

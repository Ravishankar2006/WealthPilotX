"""Deterministic offline providers.

Two jobs, both of which need data that behaves like a market without needing a
market:

* **Tests.** The suite must never depend on a third-party API — a red build caused
  by Yahoo having a bad afternoon teaches nobody anything. Because these generators
  are seeded per symbol, the same symbol yields byte-identical bars on every run, so
  integration tests can assert on actual values rather than on "some rows appeared".
* **Offline development.** `MARKET_DATA_PROVIDER=synthetic` gives a working stack with
  no network and no API keys.

The series are a seeded geometric random walk. They are *not* a simulation of
anything and must never be promoted to a data source for a model that ships — hence
the `synthetic:` prefix written into `market_data.source`, which makes any such
mistake a visible one-line query away.
"""

import random
from datetime import date, timedelta
from decimal import Decimal

from app.data.asset_universe import UNIVERSE
from app.models.enums import AssetClass, EconomicSeries
from app.providers.base import PriceBar, SeriesPoint

CENTS = Decimal("0.01")

# Rough plausibility per series, so an offline dashboard does not show a 4000%
# unemployment rate: (starting level, per-step drift, per-step volatility).
_SERIES_SHAPE: dict[EconomicSeries, tuple[float, float, float]] = {
    EconomicSeries.INFLATION: (300.0, 0.2, 0.4),
    EconomicSeries.INTEREST_RATE: (4.5, 0.0, 0.15),
    EconomicSeries.GDP: (22000.0, 15.0, 60.0),
    EconomicSeries.UNEMPLOYMENT: (4.0, 0.0, 0.12),
    EconomicSeries.FX_RATE: (120.0, 0.0, 0.8),
}


def _trading_days(start: date, end: date) -> list[date]:
    """Weekdays in [start, end].

    Public holidays are not modelled: a real calendar would make the generator's
    output depend on a holiday table that would then need maintaining, and nothing
    downstream asserts that every weekday is a session.
    """
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


# Per-asset-class daily drift and volatility, so the offline universe resembles a
# real one in the dimension the whole system keys off.
#
# Originally every symbol used one distribution, which made a treasury ETF as
# volatile as a growth stock (~19% annualised across the board). That is not a
# cosmetic problem: the risk classes target 8% / 14% / 22% volatility, so a
# LOW-risk profile had *no* suitable assets anywhere in the offline universe, and
# §18's ranking metrics reported zero relevant assets for every LOW combination.
# A synthetic universe that cannot represent a conservative portfolio cannot
# exercise the conservative path.
_CLASS_DYNAMICS: dict[AssetClass, tuple[float, float]] = {
    #                     daily drift, daily volatility  (≈ annualised)
    AssetClass.BOND: (0.00010, 0.0022),  # ~3.5%
    AssetClass.CASH: (0.00006, 0.0004),  # ~0.6%
    AssetClass.REAL_ESTATE: (0.00028, 0.0105),  # ~17%
    AssetClass.COMMODITY: (0.00018, 0.0098),  # ~16%
    AssetClass.EQUITY: (0.00035, 0.0115),  # ~18%
}

_DEFAULT_DYNAMICS = (0.0003, 0.012)

_CLASS_BY_SYMBOL: dict[str, AssetClass] = {asset.symbol: asset.asset_class for asset in UNIVERSE}


class SyntheticMarketDataProvider:
    """A seeded random walk that satisfies the OHLC invariants by construction."""

    name = "synthetic"

    def __init__(self, seed: int = 20260101) -> None:
        self._seed = seed

    def _dynamics(self, symbol: str) -> tuple[float, float]:
        """Drift and volatility for a symbol, from its class in the tracked universe.

        An unknown symbol gets the equity-ish default: a provider must still answer
        for a ticker that is not in the seed list.
        """
        asset_class = _CLASS_BY_SYMBOL.get(symbol.upper())
        if asset_class is None:
            return _DEFAULT_DYNAMICS
        return _CLASS_DYNAMICS.get(asset_class, _DEFAULT_DYNAMICS)

    def fetch_daily_bars(self, symbol: str, start: date, end: date) -> list[PriceBar]:
        # Seeded by symbol alone, and walked from a fixed epoch below, so the value
        # for a given date does not depend on the window that asked for it. Without
        # that, an incremental fetch would invent a fresh walk and the series would
        # jump at every join.
        rng = random.Random(f"{self._seed}:{symbol}")  # noqa: S311 — test data, not crypto
        drift_mean, drift_sigma = self._dynamics(symbol)
        price = 50.0 + rng.random() * 200.0

        bars: list[PriceBar] = []
        epoch = date(2015, 1, 1)
        for day in _trading_days(epoch, end):
            open_price = price
            drift = rng.gauss(drift_mean, drift_sigma)
            close_price = max(1.0, open_price * (1.0 + drift))
            high = max(open_price, close_price) * (1.0 + abs(rng.gauss(0, 0.004)))
            low = min(open_price, close_price) * (1.0 - abs(rng.gauss(0, 0.004)))
            volume = int(abs(rng.gauss(5_000_000, 1_500_000)))
            price = close_price

            if day < start:
                continue

            bars.append(
                PriceBar(
                    date=day,
                    open=Decimal(str(round(open_price, 2))).quantize(CENTS),
                    high=Decimal(str(round(high, 2))).quantize(CENTS),
                    low=Decimal(str(round(low, 2))).quantize(CENTS),
                    close=Decimal(str(round(close_price, 2))).quantize(CENTS),
                    # No dividend model, so the adjusted close equals the close.
                    adj_close=Decimal(str(round(close_price, 2))).quantize(CENTS),
                    volume=volume,
                )
            )
        return bars


class SyntheticEconomicDataProvider:
    """Monthly observations, which is the cadence of most FRED series FR-05 names."""

    name = "synthetic"

    def __init__(self, seed: int = 20260101) -> None:
        self._seed = seed

    def fetch_series(self, series: EconomicSeries, start: date, end: date) -> list[SeriesPoint]:
        rng = random.Random(f"{self._seed}:{series}")  # noqa: S311 — test data, not crypto
        level, drift, volatility = _SERIES_SHAPE[series]

        points: list[SeriesPoint] = []
        epoch = date(2015, 1, 1)
        current = epoch
        while current <= end:
            level = max(0.05, level + drift + rng.gauss(0, volatility))
            if current >= start:
                points.append(SeriesPoint(date=current, value=Decimal(str(round(level, 4)))))
            # First of the following month.
            current = (
                date(current.year + 1, 1, 1)
                if current.month == 12
                else date(current.year, current.month + 1, 1)
            )
        return points

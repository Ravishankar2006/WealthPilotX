"""The data-provider seam (PRD §7.3).

Yahoo Finance is an unofficial API that can change or disappear without notice, so
no application code is allowed to depend on its shape. Everything above this module
talks to these two protocols and these DTOs; swapping in a paid provider is then a
new file in this package and one settings change, not a refactor.

Two rules keep the seam honest:

1. Vendor objects never cross the boundary. A provider returns `PriceBar` and
   `SeriesPoint`, never a DataFrame — otherwise pandas' column naming becomes part
   of the contract and the abstraction buys nothing.
2. Vendor exceptions never cross it either. Providers translate whatever their
   client raises into the `ProviderError` hierarchy below, so callers can write one
   retry policy instead of one per vendor.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol, runtime_checkable

from app.models.enums import AssetClass, AssetType, EconomicSeries


class ProviderError(Exception):
    """Base for every failure a provider is allowed to raise."""


class ProviderUnavailableError(ProviderError):
    """Transient: network failure, timeout, 5xx. Worth retrying."""


class ProviderRateLimitedError(ProviderError):
    """Transient, but back off harder — the provider is asking us to slow down."""


class SymbolNotFoundError(ProviderError):
    """Permanent for this symbol: the provider has no such instrument.

    Not retryable — retrying a delisted ticker 5 times just delays the run.
    """


class ProviderConfigurationError(ProviderError):
    """Permanent: a missing API key or an unusable setting. Retrying cannot help."""


RETRYABLE = (ProviderUnavailableError, ProviderRateLimitedError)


@dataclass(frozen=True, slots=True)
class PriceBar:
    """One trading day for one symbol (FR-04).

    Prices are `Decimal` the whole way from provider to database. Floats accumulate
    representation error under the arithmetic M3 will do on returns, and a price is
    exactly the kind of value that should never drift.
    """

    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adj_close: Decimal
    volume: int


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    """One observation of one macroeconomic series (FR-05)."""

    date: date
    value: Decimal


@dataclass(frozen=True, slots=True)
class AssetMetadata:
    symbol: str
    name: str | None = None
    asset_type: AssetType | None = None
    asset_class: AssetClass | None = None
    currency: str | None = None
    exchange: str | None = None


@runtime_checkable
class MarketDataProvider(Protocol):
    """Historical OHLCV. The `name` is persisted on every row it produces, so a
    later data-quality question can be answered with a query rather than a guess."""

    name: str

    def fetch_daily_bars(self, symbol: str, start: date, end: date) -> list[PriceBar]:
        """Bars for `symbol` in [start, end], ascending by date.

        Raises `SymbolNotFoundError` when the instrument does not exist, and a retryable
        `ProviderError` when the provider is merely unwell. Returning `[]` means the
        symbol exists and had no trading days in the window — a market holiday
        stretch is not an error.
        """
        ...


@runtime_checkable
class EconomicDataProvider(Protocol):
    """Macroeconomic series (FR-05)."""

    name: str

    def fetch_series(self, series: EconomicSeries, start: date, end: date) -> list[SeriesPoint]:
        """Observations for `series` in [start, end], ascending by date."""
        ...

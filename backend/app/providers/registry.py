"""Provider selection (§7.3).

The single place that decides *which* implementation runs. Application and job code
asks for `get_market_provider()` and never names a vendor, so substituting a licensed
provider is a new module plus an environment variable.
"""

from app.core.config import get_settings
from app.providers.base import EconomicDataProvider, MarketDataProvider
from app.providers.synthetic import SyntheticEconomicDataProvider, SyntheticMarketDataProvider


def get_market_provider(name: str | None = None) -> MarketDataProvider:
    settings = get_settings()
    choice = (name or settings.market_data_provider).lower()

    if choice == "yahoo":
        # Imported here, not at module scope: the yahoo module pulls in pandas via
        # yfinance, and the API process has no reason to pay for that.
        from app.providers.yahoo import YahooMarketDataProvider

        return YahooMarketDataProvider()
    if choice == "synthetic":
        return SyntheticMarketDataProvider()

    raise ValueError(f"Unknown market data provider: {choice!r}")


def get_economic_provider(name: str | None = None) -> EconomicDataProvider:
    settings = get_settings()
    choice = (name or settings.economic_data_provider).lower()

    if choice == "fred":
        from app.providers.fred import FredEconomicDataProvider

        return FredEconomicDataProvider(api_key=settings.fred_api_key)
    if choice == "synthetic":
        return SyntheticEconomicDataProvider()

    raise ValueError(f"Unknown economic data provider: {choice!r}")

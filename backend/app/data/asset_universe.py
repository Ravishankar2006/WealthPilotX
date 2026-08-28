"""The tracked asset universe (Phase 2 plan §5).

Held as data rather than as database rows in a migration, so adding or retiring a
symbol is a one-line change plus a re-run of `python -m app.jobs seed-assets` — not
a schema migration. The seed is idempotent and updates metadata in place.

Composition is chosen for what M4's optimizer needs rather than for what is famous:
low-correlation blocks (equity / duration / credit / commodity / real estate) so
diversification is something the optimizer can actually find, plus a handful of
large-cap single names so the recommender is not choosing exclusively between funds.
"""

from dataclasses import dataclass

from app.models.enums import AssetClass, AssetType


@dataclass(frozen=True, slots=True)
class SeedAsset:
    symbol: str
    name: str
    asset_type: AssetType
    asset_class: AssetClass
    exchange: str = "NYSEARCA"
    currency: str = "USD"


UNIVERSE: tuple[SeedAsset, ...] = (
    # --- Broad equity ---
    SeedAsset("SPY", "SPDR S&P 500 ETF Trust", AssetType.ETF, AssetClass.EQUITY),
    SeedAsset("VTI", "Vanguard Total Stock Market ETF", AssetType.ETF, AssetClass.EQUITY),
    SeedAsset("QQQ", "Invesco QQQ Trust", AssetType.ETF, AssetClass.EQUITY, "NASDAQ"),
    SeedAsset("IWM", "iShares Russell 2000 ETF", AssetType.ETF, AssetClass.EQUITY),
    SeedAsset("VTV", "Vanguard Value ETF", AssetType.ETF, AssetClass.EQUITY),
    SeedAsset("VUG", "Vanguard Growth ETF", AssetType.ETF, AssetClass.EQUITY),
    # --- International equity ---
    SeedAsset("VXUS", "Vanguard Total International Stock ETF", AssetType.ETF, AssetClass.EQUITY),
    SeedAsset("VEA", "Vanguard FTSE Developed Markets ETF", AssetType.ETF, AssetClass.EQUITY),
    SeedAsset("VWO", "Vanguard FTSE Emerging Markets ETF", AssetType.ETF, AssetClass.EQUITY),
    # --- Treasuries (the duration ladder the optimizer needs) ---
    SeedAsset("SHY", "iShares 1-3 Year Treasury Bond ETF", AssetType.ETF, AssetClass.BOND),
    SeedAsset("IEF", "iShares 7-10 Year Treasury Bond ETF", AssetType.ETF, AssetClass.BOND),
    SeedAsset("TLT", "iShares 20+ Year Treasury Bond ETF", AssetType.ETF, AssetClass.BOND),
    # --- Credit ---
    SeedAsset("AGG", "iShares Core U.S. Aggregate Bond ETF", AssetType.ETF, AssetClass.BOND),
    SeedAsset(
        "LQD", "iShares iBoxx Investment Grade Corporate Bond ETF", AssetType.ETF, AssetClass.BOND
    ),
    SeedAsset("HYG", "iShares iBoxx High Yield Corporate Bond ETF", AssetType.ETF, AssetClass.BOND),
    SeedAsset("TIP", "iShares TIPS Bond ETF", AssetType.ETF, AssetClass.BOND),
    # --- Sector equity ---
    SeedAsset("XLK", "Technology Select Sector SPDR Fund", AssetType.ETF, AssetClass.EQUITY),
    SeedAsset("XLF", "Financial Select Sector SPDR Fund", AssetType.ETF, AssetClass.EQUITY),
    SeedAsset("XLE", "Energy Select Sector SPDR Fund", AssetType.ETF, AssetClass.EQUITY),
    SeedAsset("XLV", "Health Care Select Sector SPDR Fund", AssetType.ETF, AssetClass.EQUITY),
    SeedAsset("XLU", "Utilities Select Sector SPDR Fund", AssetType.ETF, AssetClass.EQUITY),
    # --- Commodity ---
    SeedAsset("GLD", "SPDR Gold Shares", AssetType.COMMODITY, AssetClass.COMMODITY),
    SeedAsset("SLV", "iShares Silver Trust", AssetType.COMMODITY, AssetClass.COMMODITY),
    SeedAsset(
        "DBC", "Invesco DB Commodity Index Tracking Fund", AssetType.COMMODITY, AssetClass.COMMODITY
    ),
    # --- Real estate ---
    SeedAsset("VNQ", "Vanguard Real Estate ETF", AssetType.ETF, AssetClass.REAL_ESTATE),
    # --- Large-cap equity ---
    SeedAsset("AAPL", "Apple Inc.", AssetType.EQUITY, AssetClass.EQUITY, "NASDAQ"),
    SeedAsset("MSFT", "Microsoft Corporation", AssetType.EQUITY, AssetClass.EQUITY, "NASDAQ"),
    SeedAsset("JNJ", "Johnson & Johnson", AssetType.EQUITY, AssetClass.EQUITY, "NYSE"),
    SeedAsset("JPM", "JPMorgan Chase & Co.", AssetType.EQUITY, AssetClass.EQUITY, "NYSE"),
    SeedAsset("XOM", "Exxon Mobil Corporation", AssetType.EQUITY, AssetClass.EQUITY, "NYSE"),
    SeedAsset("PG", "Procter & Gamble Company", AssetType.EQUITY, AssetClass.EQUITY, "NYSE"),
    SeedAsset("KO", "Coca-Cola Company", AssetType.EQUITY, AssetClass.EQUITY, "NYSE"),
)

SYMBOLS: tuple[str, ...] = tuple(asset.symbol for asset in UNIVERSE)

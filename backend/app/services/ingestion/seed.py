"""Seeding the tracked asset universe.

Idempotent by symbol so the job can run on every deploy: new symbols are inserted,
existing ones have their metadata refreshed, and nothing is ever deleted — an asset
dropped from the list keeps its price history, which M3 still needs for a backtest
that spans the period when it was tracked.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger, safe_extra
from app.data.asset_universe import UNIVERSE, SeedAsset
from app.models.asset import Asset

logger = get_logger(__name__)


def seed_assets(db: Session, universe: tuple[SeedAsset, ...] = UNIVERSE) -> tuple[int, int]:
    """Insert or refresh the universe. Returns (created, updated)."""
    existing = {asset.symbol: asset for asset in db.scalars(select(Asset))}
    created = updated = 0

    for seed in universe:
        asset = existing.get(seed.symbol)
        if asset is None:
            db.add(
                Asset(
                    symbol=seed.symbol,
                    name=seed.name,
                    asset_type=seed.asset_type,
                    asset_class=seed.asset_class,
                    currency=seed.currency,
                    exchange=seed.exchange,
                    is_active=True,
                )
            )
            created += 1
            continue

        changed = (
            asset.name != seed.name
            or asset.asset_type != seed.asset_type
            or asset.asset_class != seed.asset_class
            or asset.currency != seed.currency
            or asset.exchange != seed.exchange
            or not asset.is_active
        )
        if changed:
            asset.name = seed.name
            asset.asset_type = seed.asset_type
            asset.asset_class = seed.asset_class
            asset.currency = seed.currency
            asset.exchange = seed.exchange
            # Re-adding a symbol to the list reactivates it rather than creating a
            # second row, which would orphan the first one's history.
            asset.is_active = True
            updated += 1

    db.commit()
    logger.info(
        "assets_seeded",
        extra=safe_extra(created=created, updated=updated, total=len(universe)),
    )
    return created, updated

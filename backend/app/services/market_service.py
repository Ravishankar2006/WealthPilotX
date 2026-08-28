"""Read path for the market endpoints (§13.2).

Keyset pagination throughout. Both list queries fetch `limit + 1` rows and use the
presence of the extra one to decide whether there is a next page — which avoids a
second COUNT query and, more importantly, never claims a next page that turns out
to be empty.
"""

import uuid
from collections.abc import Callable
from datetime import date as date_type

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.pagination import decode_cursor, encode_cursor
from app.models.asset import Asset
from app.models.enums import AssetClass, AssetType
from app.models.market_data import MarketData


def _page[T](
    rows: list[T], limit: int, cursor_of: Callable[[T], str]
) -> tuple[list[T], str | None]:
    """Trim the sentinel row and derive the next cursor from the last kept row."""
    if len(rows) <= limit:
        return rows, None
    kept = rows[:limit]
    return kept, encode_cursor(cursor_of(kept[-1]))


def list_assets(
    db: Session,
    *,
    limit: int,
    cursor: str | None = None,
    asset_type: AssetType | None = None,
    asset_class: AssetClass | None = None,
    include_inactive: bool = False,
) -> tuple[list[Asset], str | None]:
    """Assets ordered by symbol; the cursor is the last symbol of the previous page.

    Symbol is the pagination key because it is unique and stable — an ordering on
    `name` or `created_at` could tie, and ties make keyset pagination drop rows.
    """
    statement: Select[tuple[Asset]] = select(Asset).order_by(Asset.symbol)

    if not include_inactive:
        statement = statement.where(Asset.is_active.is_(True))
    if asset_type is not None:
        statement = statement.where(Asset.asset_type == asset_type)
    if asset_class is not None:
        statement = statement.where(Asset.asset_class == asset_class)
    if cursor:
        statement = statement.where(Asset.symbol > decode_cursor(cursor))

    rows = list(db.scalars(statement.limit(limit + 1)))
    return _page(rows, limit, lambda asset: asset.symbol)


def get_asset(db: Session, symbol: str) -> Asset:
    asset = db.scalar(select(Asset).where(Asset.symbol == symbol.upper()))
    if asset is None:
        raise AppError(
            404,
            "asset_not_found",
            f"No tracked asset with symbol {symbol.upper()!r}.",
        )
    return asset


def list_bars(
    db: Session,
    asset_id: uuid.UUID,
    *,
    limit: int,
    cursor: str | None = None,
    start: date_type | None = None,
    end: date_type | None = None,
) -> tuple[list[MarketData], str | None]:
    """Bars newest-first — the order a chart or a "latest close" lookup wants.

    Descending order makes the cursor an exclusive upper bound on date, so paging
    walks backwards through history.
    """
    statement = (
        select(MarketData).where(MarketData.asset_id == asset_id).order_by(MarketData.date.desc())
    )

    if start is not None:
        statement = statement.where(MarketData.date >= start)
    if end is not None:
        statement = statement.where(MarketData.date <= end)
    if cursor:
        # The cursor decodes to a plausible string but not necessarily to a date —
        # a hand-edited one would otherwise raise ValueError and surface as a 500.
        try:
            after = date_type.fromisoformat(decode_cursor(cursor))
        except ValueError as exc:
            raise AppError(400, "invalid_cursor", "The pagination cursor is not valid.") from exc
        statement = statement.where(MarketData.date < after)

    rows = list(db.scalars(statement.limit(limit + 1)))
    return _page(rows, limit, lambda bar: bar.date.isoformat())

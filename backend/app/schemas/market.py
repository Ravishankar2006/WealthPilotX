"""Request and response shapes for the market endpoints (§13.2)."""

import uuid
from datetime import date as date_type
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AssetClass, AssetType


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    symbol: str
    name: str | None
    asset_type: AssetType
    asset_class: AssetClass
    currency: str
    exchange: str | None
    is_active: bool


class PriceBarOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: date_type
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adj_close: Decimal
    volume: int


class AssetListResponse(BaseModel):
    """The §13.1 list envelope."""

    data: list[AssetOut]
    next_cursor: str | None = None


class MarketHistoryResponse(BaseModel):
    """§13.1's `data` / `next_cursor` envelope, plus the asset the bars belong to.

    The extra key is additive, not a deviation: a chart needs the instrument's name
    and class alongside its prices, and making the client issue a second request to
    /market/assets for every symbol it draws would be a worse contract.
    """

    asset: AssetOut
    data: list[PriceBarOut]
    next_cursor: str | None = None


class MarketQuery(BaseModel):
    limit: int = Field(default=50, ge=1, le=500)
    cursor: str | None = None

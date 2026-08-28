"""FR-04 / §13.2 read endpoints for stored market data.

Authenticated like everything else outside `/auth/register` and `/auth/login`
(§13.1). The data itself is public, but the convention is not conditional on how
sensitive a payload happens to be, and a uniform rule is one fewer thing to get
wrong when an endpoint later starts returning something personal.

`GET /market/{symbol}/prediction` is not here: it needs the XGBoost model from M3.
"""

from datetime import date as date_type
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.core.errors import AppError
from app.core.pagination import DEFAULT_LIMIT, MAX_LIMIT
from app.models.enums import AssetClass, AssetType
from app.schemas.common import ErrorResponse
from app.schemas.market import (
    AssetListResponse,
    AssetOut,
    MarketHistoryResponse,
    PriceBarOut,
)
from app.services import market_service

router = APIRouter(prefix="/market", tags=["market"])

Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


@router.get(
    "/assets",
    response_model=AssetListResponse,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
def list_assets(
    user: CurrentUser,
    db: DbSession,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
    asset_type: AssetType | None = None,
    asset_class: AssetClass | None = None,
) -> AssetListResponse:
    """The tracked universe, ordered by symbol.

    `asset_class` is the filter M4's optimizer will use for its per-class weight
    caps (FR-11); `asset_type` describes the instrument instead.
    """
    assets, next_cursor = market_service.list_assets(
        db, limit=limit, cursor=cursor, asset_type=asset_type, asset_class=asset_class
    )
    return AssetListResponse(
        data=[AssetOut.model_validate(asset) for asset in assets],
        next_cursor=next_cursor,
    )


@router.get(
    "/{symbol}",
    response_model=MarketHistoryResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
def read_market_history(
    symbol: str,
    user: CurrentUser,
    db: DbSession,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
    start: date_type | None = None,
    end: date_type | None = None,
) -> MarketHistoryResponse:
    """Stored OHLCV for one symbol, newest first.

    Newest-first because both callers want that end of the series: a chart renders
    the recent window, and "the latest close" is then the first row rather than a
    full scan. Paging walks backwards through history from there.
    """
    if start and end and start > end:
        raise AppError(
            400,
            "invalid_date_range",
            "`start` must not be after `end`.",
            {"start": ["start must not be after end"]},
        )

    asset = market_service.get_asset(db, symbol)
    bars, next_cursor = market_service.list_bars(
        db, asset.id, limit=limit, cursor=cursor, start=start, end=end
    )
    return MarketHistoryResponse(
        asset=AssetOut.model_validate(asset),
        data=[PriceBarOut.model_validate(bar) for bar in bars],
        next_cursor=next_cursor,
    )

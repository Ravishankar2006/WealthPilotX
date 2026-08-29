"""FR-04 / §13.2 read endpoints for stored market data.

Authenticated like everything else outside `/auth/register` and `/auth/login`
(§13.1). The data itself is public, but the convention is not conditional on how
sensitive a payload happens to be, and a uniform rule is one fewer thing to get
wrong when an endpoint later starts returning something personal.

`GET /market/{symbol}/prediction` also carries FR-09's asset-analysis metrics: they
are the same feature-pipeline outputs for the same asset, and §13.2 lists no separate
asset-analysis route to put them on.
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
from app.schemas.risk import (
    FeatureContributionOut,
    PredictionExplanationOut,
    PredictionOut,
)
from app.services import explanation_service, market_service, prediction_service

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
    "/{symbol}/prediction",
    response_model=PredictionOut,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
def read_prediction(symbol: str, user: CurrentUser, db: DbSession) -> PredictionOut:
    """FR-08's prediction plus FR-09's asset metrics.

    Reads the stored prediction rather than running the model per request: §16.1
    allows 5 seconds for an ML prediction, but the feature pipeline needs a year of
    history per asset, and recomputing that on every dashboard load would spend it
    all on work the nightly job already did.
    """
    asset = market_service.get_asset(db, symbol)
    try:
        return prediction_service.asset_prediction(db, asset)
    except prediction_service.NoPredictionError as exc:
        raise AppError(
            404,
            "no_prediction",
            f"No prediction is available for {asset.symbol}: {exc.reason}.",
        ) from exc


@router.get(
    "/{symbol}/prediction/explanation",
    response_model=PredictionExplanationOut,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def read_prediction_explanation(
    symbol: str, user: CurrentUser, db: DbSession
) -> PredictionExplanationOut:
    """FR-13's advanced explanation: what moved this prediction, and by how much.

    Registered before `/{symbol}` because FastAPI matches routes in declaration
    order and `/{symbol}` would otherwise swallow the longer path.

    Note the parallel with `read_prediction`'s docstring: this one *does* run the
    model, because a Shapley decomposition is not something the nightly job can
    precompute for every asset and store cheaply. It stays inside §16.1's 5-second
    prediction budget because it is one row through one booster — the expensive part
    of a prediction is assembling the features, and that work is the same either way.
    """
    asset = market_service.get_asset(db, symbol)
    try:
        attribution = explanation_service.explain_prediction(db, asset)
    except prediction_service.NoPredictionError as exc:
        raise AppError(
            404,
            "no_prediction",
            f"No prediction is available for {asset.symbol}: {exc.reason}.",
        ) from exc

    return PredictionExplanationOut(
        symbol=attribution.symbol,
        prediction_date=attribution.prediction_date,
        horizon_days=attribution.horizon_days,
        model_version=attribution.model_version,
        predicted_return=attribution.predicted_return,
        base_value=attribution.base_value,
        contributions=[
            FeatureContributionOut(
                feature=item.feature,
                label=item.label,
                # NaN is not valid JSON. A macro feature can legitimately be missing
                # for a given day, and the booster still attributes to it, so the
                # contribution is real even when the value behind it is not known.
                value=None if item.value != item.value else item.value,
                contribution=item.contribution,
                direction=item.direction,
            )
            for item in attribution.contributions
        ],
        contributions_shown=len(attribution.contributions),
        contributions_total=attribution.contributions_total,
        reproduced=attribution.reproduced,
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

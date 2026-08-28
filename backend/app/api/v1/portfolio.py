"""FR-10 to FR-12 — portfolio endpoints (§13.2).

`/portfolio/generate` runs the optimizer, so §13.1 puts it in the 10 req/min
expensive bucket, keyed per user.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DbSession, UserRateLimit
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, decode_cursor, encode_cursor
from app.models.asset import Asset
from app.models.portfolio import Portfolio, PortfolioAsset
from app.models.recommendation import Recommendation
from app.schemas.common import ErrorResponse
from app.schemas.portfolio import HoldingOut, PortfolioListResponse, PortfolioOut
from app.services import portfolio_service, profile_service

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

_settings = get_settings()
expensive = UserRateLimit("expensive", _settings.rate_limit_expensive_per_minute)

Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


def _to_out(db: Session, portfolio: Portfolio) -> PortfolioOut:
    """Assemble a portfolio with its holdings and their reasons.

    Reasons are joined in rather than left to a second request: FR-13 requires a
    reason to be attached *before* a recommendation is shown, and a shape that makes
    it optional invites a caller to render the weights without them.
    """
    rows = db.execute(
        select(PortfolioAsset.weight, Asset.symbol, Asset.name, Asset.asset_class, Asset.id)
        .join(Asset, Asset.id == PortfolioAsset.asset_id)
        .where(PortfolioAsset.portfolio_id == portfolio.id)
        .order_by(PortfolioAsset.weight.desc())
    ).all()

    reasons = {
        rec.asset_id: rec
        for rec in db.scalars(
            select(Recommendation).where(Recommendation.portfolio_id == portfolio.id)
        )
    }

    holdings = []
    for weight, symbol, name, asset_class, asset_id in rows:
        recommendation = reasons.get(asset_id)
        holdings.append(
            HoldingOut(
                symbol=symbol,
                name=name,
                asset_class=asset_class,
                weight=weight,
                reason=recommendation.reason if recommendation else None,
                recommendation_id=recommendation.id if recommendation else None,
            )
        )

    objective = portfolio.objective or {}
    return PortfolioOut(
        id=portfolio.id,
        risk_category=portfolio.risk_category,
        expected_return=portfolio.expected_return,
        expected_risk=portfolio.expected_risk,
        model_version=portfolio.model_version,
        created_at=portfolio.created_at,
        holdings=holdings,
        objective=objective,
        explanation=objective.get("summary"),
    )


@router.post(
    "/generate",
    response_model=PortfolioOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(expensive)],
    responses={
        401: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def generate(user: CurrentUser, db: DbSession) -> PortfolioOut:
    """Generate a portfolio (FR-10, FR-11).

    Every call creates a new portfolio; nothing is edited in place. An explanation
    has to still be true later, and it cannot be if what it explains has changed.
    """
    profile = profile_service.get_profile(db, user.id)
    completeness = profile_service.completeness(profile)
    if not completeness.complete or profile is None:
        raise AppError(
            422,
            "incomplete_profile",
            "Your financial profile is incomplete, so a portfolio cannot be generated.",
            {"missing_fields": completeness.missing_fields},
        )

    result = portfolio_service.generate(db, user.id, profile)
    return _to_out(db, result.portfolio)


@router.get(
    "/current",
    response_model=PortfolioOut,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
def current(user: CurrentUser, db: DbSession) -> PortfolioOut:
    portfolio = portfolio_service.current(db, user.id)
    if portfolio is None:
        raise AppError(
            404,
            "no_portfolio",
            "No portfolio has been generated for this account yet.",
        )
    return _to_out(db, portfolio)


@router.get(
    "/history",
    response_model=PortfolioListResponse,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
def history(
    user: CurrentUser,
    db: DbSession,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> PortfolioListResponse:
    """Newest first, cursor-paginated per §13.1.

    The cursor is the previous page's last `created_at`; keyset rather than offset,
    for the same reason as the market endpoints — new portfolios are inserted while
    a client pages, and offset would silently skip rows.
    """
    statement = (
        select(Portfolio).where(Portfolio.user_id == user.id).order_by(Portfolio.created_at.desc())
    )

    if cursor:
        from datetime import datetime

        try:
            after = datetime.fromisoformat(decode_cursor(cursor))
        except ValueError as exc:
            raise AppError(400, "invalid_cursor", "The pagination cursor is not valid.") from exc
        statement = statement.where(Portfolio.created_at < after)

    rows = list(db.scalars(statement.limit(limit + 1)))
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].created_at.isoformat())

    return PortfolioListResponse(
        data=[_to_out(db, portfolio) for portfolio in rows], next_cursor=next_cursor
    )

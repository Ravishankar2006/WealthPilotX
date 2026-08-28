"""FR-13 — `GET /recommendation/{id}/explanation` (§13.2)."""

import uuid

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models.asset import Asset
from app.models.portfolio import Portfolio, PortfolioAsset
from app.schemas.common import ErrorResponse
from app.schemas.portfolio import ExplanationOut
from app.services import portfolio_service

router = APIRouter(prefix="/recommendation", tags=["recommendation"])


@router.get(
    "/{recommendation_id}/explanation",
    response_model=ExplanationOut,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
def explanation(recommendation_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ExplanationOut:
    """Why this asset was recommended.

    Scoped to the caller: another user's recommendation is a 404, not a 403. Whether
    someone else holds a recommendation is not ours to confirm (§16.2).
    """
    recommendation = portfolio_service.get_recommendation(db, user.id, recommendation_id)

    symbol = db.scalar(select(Asset.symbol).where(Asset.id == recommendation.asset_id))

    weight = None
    portfolio_explanation = None
    if recommendation.portfolio_id is not None:
        weight = db.scalar(
            select(PortfolioAsset.weight).where(
                PortfolioAsset.portfolio_id == recommendation.portfolio_id,
                PortfolioAsset.asset_id == recommendation.asset_id,
            )
        )
        objective = db.scalar(
            select(Portfolio.objective).where(Portfolio.id == recommendation.portfolio_id)
        )
        portfolio_explanation = (objective or {}).get("summary")

    return ExplanationOut(
        recommendation_id=recommendation.id,
        symbol=symbol or "",
        score=recommendation.score,
        reason=recommendation.reason,
        model_version=recommendation.model_version,
        portfolio_id=recommendation.portfolio_id,
        weight=weight,
        portfolio_explanation=portfolio_explanation,
        created_at=recommendation.created_at,
    )

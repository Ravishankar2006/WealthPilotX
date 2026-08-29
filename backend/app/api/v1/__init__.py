from fastapi import APIRouter

from app.api.v1 import (
    auth,
    fairness,
    health,
    market,
    metrics,
    portfolio,
    recommendation,
    risk,
    user,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(metrics.router)
api_router.include_router(auth.router)
api_router.include_router(user.router)
api_router.include_router(market.router)
api_router.include_router(risk.router)
api_router.include_router(portfolio.router)
api_router.include_router(recommendation.router)
api_router.include_router(fairness.router)

__all__ = ["api_router"]

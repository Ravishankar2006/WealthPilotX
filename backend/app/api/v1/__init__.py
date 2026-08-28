from fastapi import APIRouter

from app.api.v1 import auth, health, market, risk, user

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(user.router)
api_router.include_router(market.router)
api_router.include_router(risk.router)

__all__ = ["api_router"]

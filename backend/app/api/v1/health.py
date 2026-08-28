"""Liveness and readiness (PRD §13.2, §16.4)."""

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.api.deps import DbSession
from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.common import HealthResponse

router = APIRouter(tags=["system"])
logger = get_logger(__name__)


@router.get("/health", response_model=HealthResponse)
def health(db: DbSession, response: Response) -> HealthResponse:
    """Readiness, not just liveness: reports 503 when the database is unreachable
    so an orchestrator stops routing traffic here."""
    settings = get_settings()
    try:
        db.execute(text("SELECT 1"))
        database = "up"
    except Exception:
        logger.exception("health_check_database_unreachable")
        database = "down"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status="ok" if database == "up" else "degraded",
        database=database,
        environment=settings.environment,
    )

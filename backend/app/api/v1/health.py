"""Liveness and readiness (PRD §13.2, §16.4), and FR-04's ingestion alert."""

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.api.deps import DbSession
from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.common import HealthResponse, IngestionHealth
from app.services.ingestion.runs import ingestion_health

router = APIRouter(tags=["system"])
logger = get_logger(__name__)


@router.get("/health", response_model=HealthResponse)
def health(db: DbSession, response: Response) -> HealthResponse:
    """Readiness, not just liveness: reports 503 when the database is unreachable
    so an orchestrator stops routing traffic here.

    Stale or failed ingestion (FR-04) reports `degraded` but keeps HTTP 200. The
    status code answers "should traffic come here?", and the answer is yes — the
    API serves yesterday's stored data perfectly well. Returning 503 would take a
    healthy API out of rotation over a background job, turning a data-freshness
    problem into an outage. The `ingestion` block is what a monitor alerts on.
    """
    settings = get_settings()
    ingestion: IngestionHealth | None = None

    try:
        db.execute(text("SELECT 1"))
        database = "up"
    except Exception:
        logger.exception("health_check_database_unreachable")
        database = "down"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    if database == "up":
        ingestion = IngestionHealth.model_validate(
            ingestion_health(db, stale_after_hours=settings.market_data_stale_after_hours)
        )

    if database == "down":
        overall = "degraded"
    elif ingestion is not None and not ingestion.healthy:
        overall = "degraded"
    else:
        overall = "ok"

    return HealthResponse(
        status=overall,
        database=database,
        environment=settings.environment,
        ingestion=ingestion,
    )

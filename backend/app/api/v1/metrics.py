"""§16.4 — `GET /api/v1/metrics`.

Authenticated, like everything except register and login. §13.1 states that rule
without exception, and an unauthenticated metrics endpoint would be the one place
the API contradicted its own convention — a scraper does not need a carve-out
badly enough to justify that. The trade is real and is recorded in
`Docs/SECURITY-REVIEW.md`: a Prometheus-style scraper needs a token, and this
project has no service-account concept to issue one from.

The payload is aggregate only. See `services/metrics_service.py` for what is
deliberately not counted.
"""

from typing import Any

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.schemas.common import ErrorResponse
from app.services import metrics_service

router = APIRouter(tags=["system"])


@router.get("/metrics", responses={401: {"model": ErrorResponse}})
def read_metrics(user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """Counters for this process, plus the ingestion success rate from the database.

    No `response_model`: the shape is a dict of series whose keys are route
    templates and timer names, which a Pydantic model would either flatten into
    `dict[str, Any]` anyway or freeze against every future counter.
    """
    return metrics_service.build_snapshot(db)

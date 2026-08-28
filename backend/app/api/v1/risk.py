"""FR-03 — risk assessment endpoints (§13.2).

`/risk/analyze` runs a model, so §13.1 puts it in the 10 req/min expensive bucket
rather than the default 100 — keyed per user, via `UserRateLimit`. See the note there
for why the plain `RateLimit` silently degrades to per-IP on an authenticated route.
"""

from fastapi import APIRouter, Depends, status

from app.api.deps import CurrentUser, DbSession, UserRateLimit
from app.core.config import get_settings
from app.core.errors import AppError
from app.schemas.common import ErrorResponse
from app.schemas.risk import RiskAssessmentOut
from app.services import risk_service

router = APIRouter(prefix="/risk", tags=["risk"])

_settings = get_settings()
expensive = UserRateLimit("expensive", _settings.rate_limit_expensive_per_minute)


@router.post(
    "/analyze",
    response_model=RiskAssessmentOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(expensive)],
    responses={
        401: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def analyze(user: CurrentUser, db: DbSession) -> RiskAssessmentOut:
    """Classify the caller's profile (FR-03).

    Scoped to the authenticated user; there is no path that names another user's
    profile, which satisfies §16.2 by construction rather than by a check.
    """
    return RiskAssessmentOut.model_validate(risk_service.assess(db, user.id))


@router.get(
    "/latest",
    response_model=RiskAssessmentOut,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
def latest(user: CurrentUser, db: DbSession) -> RiskAssessmentOut:
    assessment = risk_service.latest(db, user.id)
    if assessment is None:
        raise AppError(
            404,
            "no_risk_assessment",
            "No risk assessment has been produced for this account yet.",
        )
    return RiskAssessmentOut.model_validate(assessment)

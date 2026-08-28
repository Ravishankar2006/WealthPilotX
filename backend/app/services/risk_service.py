"""Risk assessment orchestration (FR-03).

Ties together the profile-completeness gate FR-02 requires, the production model
resolved from the registry, and the append-only assessment history.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger, safe_extra
from app.ml import registry
from app.ml.risk import model as risk_model
from app.models.financial_profile import FinancialProfile
from app.models.model_record import RISK_MODEL
from app.models.risk_assessment import RiskAssessment
from app.services import profile_service

logger = get_logger(__name__)


def assess(db: Session, user_id: uuid.UUID) -> RiskAssessment:
    """Run the risk model for one user and store the result.

    Raises 422 when the profile is incomplete — FR-02's first acceptance criterion
    requires the request to be blocked with the missing fields listed, and the
    completeness helper built in M1 is exactly what it said it was for.
    """
    profile = profile_service.get_profile(db, user_id)
    completeness = profile_service.completeness(profile)

    if not completeness.complete or profile is None:
        raise AppError(
            422,
            "incomplete_profile",
            "Your financial profile is incomplete, so a risk assessment cannot be produced.",
            {"missing_fields": completeness.missing_fields},
        )

    loaded = registry.resolve_production(db, RISK_MODEL)
    artifact: risk_model.RiskArtifact = loaded.payload

    result = risk_model.classify(
        artifact,
        age=profile.age,
        income=profile.income,
        savings=profile.savings,
        risk_appetite=profile.risk_appetite,
        investment_horizon=profile.investment_horizon,
        experience=profile.experience,
        financial_literacy=profile.financial_literacy,
    )

    assessment = RiskAssessment(
        user_id=user_id,
        model_version=loaded.version,
        risk_score=result.score,
        risk_category=result.category,
        top_factors=result.top_factors,
    )
    db.add(assessment)
    db.commit()
    db.refresh(assessment)

    # Category and model version only. The score is derived from income and savings,
    # so it stays out of the logs with them (§11.2).
    logger.info(
        "risk_assessed",
        extra=safe_extra(
            user_id=str(user_id),
            category=str(result.category),
            model_version=loaded.version,
        ),
    )
    return assessment


def latest(db: Session, user_id: uuid.UUID) -> RiskAssessment | None:
    """The newest assessment for one user. Scoped by user_id, so there is no path
    that reads another user's result (§16.2)."""
    return db.scalar(
        select(RiskAssessment)
        .where(RiskAssessment.user_id == user_id)
        .order_by(RiskAssessment.created_at.desc())
        .limit(1)
    )


def profile_for(db: Session, user_id: uuid.UUID) -> FinancialProfile | None:
    return profile_service.get_profile(db, user_id)

"""Financial profile logic (FR-02) and the §11.2 erasure path."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger, safe_extra
from app.models.financial_profile import FinancialProfile
from app.models.user import User
from app.schemas.profile import FinancialProfileIn, ProfileCompleteness

logger = get_logger(__name__)

# The fields FR-03's risk assessment will require. Kept as data so Milestone 3
# consumes the same list the onboarding form is validated against.
REQUIRED_FIELDS = (
    "age",
    "income",
    "savings",
    "risk_appetite",
    "investment_goal",
    "investment_horizon",
    "experience",
    "financial_literacy",
)


def get_profile(db: Session, user_id: uuid.UUID) -> FinancialProfile | None:
    return db.scalar(select(FinancialProfile).where(FinancialProfile.user_id == user_id))


def upsert_profile(
    db: Session, user_id: uuid.UUID, payload: FinancialProfileIn
) -> tuple[FinancialProfile, bool]:
    """Create or replace the caller's single profile. Returns (profile, created)."""
    profile = get_profile(db, user_id)
    created = profile is None

    if profile is None:
        profile = FinancialProfile(user_id=user_id)
        db.add(profile)

    for field, value in payload.model_dump().items():
        setattr(profile, field, value)

    db.commit()
    db.refresh(profile)

    # Field names only — never the values (§11.2). `safe_extra` guards against
    # reserved LogRecord names such as `created`, which raise inside logging.
    logger.info("profile_saved", extra=safe_extra(user_id=str(user_id), created=created))
    return profile, created


def completeness(profile: FinancialProfile | None) -> ProfileCompleteness:
    """What FR-03 calls to decide whether risk assessment may run."""
    if profile is None:
        return ProfileCompleteness(complete=False, missing_fields=list(REQUIRED_FIELDS))

    missing = [f for f in REQUIRED_FIELDS if getattr(profile, f, None) is None]
    return ProfileCompleteness(complete=not missing, missing_fields=missing)


def erase_user(db: Session, user: User) -> None:
    """Right to erasure (§11.2).

    Deletes the account outright rather than soft-deleting it. Cascades remove the
    profile and every refresh token; a soft delete would leave financial data in
    place while claiming it was erased.
    """
    user_id = user.id
    db.delete(user)
    db.commit()
    logger.info("user_erased", extra=safe_extra(user_id=str(user_id)))

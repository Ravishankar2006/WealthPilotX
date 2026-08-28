"""FR-02 — the financial profile, plus the §11.2 erasure endpoint."""

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession
from app.core.errors import AppError
from app.schemas.common import ErrorResponse, MessageResponse
from app.schemas.profile import FinancialProfileIn, FinancialProfileOut, ProfileCompleteness
from app.services import profile_service

router = APIRouter(prefix="/user", tags=["user"])


@router.get(
    "/profile",
    response_model=FinancialProfileOut,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
def read_profile(user: CurrentUser, db: DbSession) -> FinancialProfileOut:
    profile = profile_service.get_profile(db, user.id)
    if profile is None:
        raise AppError(
            404,
            "profile_not_found",
            "No financial profile has been created for this account yet.",
        )
    return FinancialProfileOut.model_validate(profile)


@router.put(
    "/profile",
    response_model=FinancialProfileOut,
    responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
def upsert_profile(
    payload: FinancialProfileIn, user: CurrentUser, db: DbSession
) -> FinancialProfileOut:
    """One profile per account, so this replaces rather than appends. The route is
    scoped to the authenticated user — there is no path that names another user's
    profile, which is the cheapest way to satisfy §16.2's ownership rule."""
    profile, _ = profile_service.upsert_profile(db, user.id, payload)
    return FinancialProfileOut.model_validate(profile)


@router.get("/profile/completeness", response_model=ProfileCompleteness)
def profile_completeness(user: CurrentUser, db: DbSession) -> ProfileCompleteness:
    """Drives onboarding state now; gates risk assessment from Milestone 3 (FR-03)."""
    return profile_service.completeness(profile_service.get_profile(db, user.id))


@router.delete(
    "/profile",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    responses={401: {"model": ErrorResponse}},
)
def erase_account(user: CurrentUser, db: DbSession) -> MessageResponse:
    """Right to erasure (§11.2). Removes the account, the financial profile and
    every session in one operation — not a soft delete."""
    profile_service.erase_user(db, user)
    return MessageResponse(message="Your account and all associated data have been deleted.")

"""FR-01 — registration, login, refresh, logout."""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import DbSession
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.ratelimit import RateLimit
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    TokenPair,
    UserOut,
)
from app.schemas.common import ErrorResponse, MessageResponse
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["authentication"])
logger = get_logger(__name__)

_settings = get_settings()

# Credential endpoints get the expensive bucket: they are the ones worth
# brute-forcing, and argon2 verification is deliberately costly.
_credential_limit = RateLimit("auth", _settings.rate_limit_expensive_per_minute)


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
def register(
    payload: RegisterRequest,
    db: DbSession,
    _: Annotated[None, Depends(_credential_limit)],
) -> RegisterResponse:
    user = auth_service.register_user(db, payload.email, payload.password)
    tokens = auth_service.issue_token_pair(db, user)
    logger.info("user_registered", extra={"user_id": str(user.id)})
    return RegisterResponse(user=UserOut.model_validate(user), tokens=tokens)


@router.post("/login", response_model=TokenPair, responses={401: {"model": ErrorResponse}})
def login(
    payload: LoginRequest,
    db: DbSession,
    _: Annotated[None, Depends(_credential_limit)],
) -> TokenPair:
    user = auth_service.authenticate(db, payload.email, payload.password)
    logger.info("user_logged_in", extra={"user_id": str(user.id)})
    return auth_service.issue_token_pair(db, user)


@router.post("/refresh", response_model=TokenPair, responses={401: {"model": ErrorResponse}})
def refresh(payload: RefreshRequest, db: DbSession) -> TokenPair:
    return auth_service.rotate_refresh_token(db, payload.refresh_token)


@router.post("/logout", response_model=MessageResponse)
def logout(payload: RefreshRequest, db: DbSession) -> MessageResponse:
    """Revokes the token family. Succeeds even on an unknown token — the caller's
    intent is met either way, and a 404 here would leak which tokens exist."""
    auth_service.logout(db, payload.refresh_token)
    return MessageResponse(message="Signed out.")

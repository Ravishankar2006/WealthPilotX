"""Shared route dependencies."""

import uuid
from typing import Annotated

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import decode_token
from app.db.session import get_db
from app.models.user import User

# auto_error=False so a missing header reaches our handler and returns the §13.1
# envelope rather than FastAPI's default `{"detail": ...}` shape.
_bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]


def _unauthorized(message: str) -> AppError:
    return AppError(401, "unauthorized", message)


def get_current_user(
    request: Request,
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None or not credentials.credentials:
        raise _unauthorized("Authentication required.")

    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("The access token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise _unauthorized("The access token is not valid.") from exc

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise _unauthorized("The access token is not valid.") from exc

    user = db.get(User, user_id)
    if user is None:
        # Token signed correctly but the account is gone — an erased user (§11.2)
        # holding a still-valid access token.
        raise _unauthorized("The account no longer exists.")

    # Lets the rate limiter key on the user rather than the source address.
    request.state.user_id = str(user.id)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]

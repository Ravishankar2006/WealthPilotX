"""Authentication logic (FR-01).

Refresh tokens are opaque random strings, stored only as SHA-256 hashes, and
rotated on every use. Reuse of an already-rotated token revokes its entire family
— the standard defence against a stolen refresh token, and the reason `family_id`
exists on the model.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.security import create_access_token, hash_password, needs_rehash, verify_password
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import TokenPair

logger = get_logger(__name__)

REFRESH_TOKEN_BYTES = 32


def _hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalise_email(email: str) -> str:
    return email.strip().lower()


def _issue_refresh_token(db: Session, user_id: uuid.UUID, family_id: uuid.UUID) -> str:
    settings = get_settings()
    raw = secrets.token_urlsafe(REFRESH_TOKEN_BYTES)
    db.add(
        RefreshToken(
            user_id=user_id,
            family_id=family_id,
            token_hash=_hash_refresh_token(raw),
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days),
        )
    )
    return raw


def issue_token_pair(db: Session, user: User, family_id: uuid.UUID | None = None) -> TokenPair:
    access_token, expires_at = create_access_token(user.id)
    refresh_token = _issue_refresh_token(db, user.id, family_id or uuid.uuid4())
    db.commit()
    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
    )


def register_user(db: Session, email: str, password: str) -> User:
    user = User(
        email=normalise_email(email),
        password_hash=hash_password(password),
        tos_accepted_at=datetime.now(UTC),
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        # §13.1 — 409 on a duplicate email, per FR-01's acceptance criteria.
        raise AppError(
            status_code=409,
            code="email_already_registered",
            message="An account with that email address already exists.",
        ) from exc
    return user


def authenticate(db: Session, email: str, password: str) -> User:
    user = db.scalar(select(User).where(User.email == normalise_email(email)))

    if user is None:
        # Hash anyway so a missing account and a wrong password take comparable
        # time; otherwise the endpoint becomes an account-enumeration oracle.
        hash_password(password)
        raise AppError(401, "invalid_credentials", "Email or password is incorrect.")

    if not verify_password(password, user.password_hash):
        raise AppError(401, "invalid_credentials", "Email or password is incorrect.")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        db.flush()

    return user


def rotate_refresh_token(db: Session, raw_token: str) -> TokenPair:
    token_hash = _hash_refresh_token(raw_token)
    record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))

    if record is None:
        raise AppError(401, "invalid_refresh_token", "The refresh token is not valid.")

    now = datetime.now(UTC)

    if record.revoked_at is not None:
        # Already rotated or logged out, yet presented again — treat the family as
        # compromised and revoke all of it, forcing a fresh login.
        revoke_family(db, record.family_id)
        db.commit()
        logger.warning(
            "refresh_token_reuse_detected",
            extra={"user_id": str(record.user_id), "family_id": str(record.family_id)},
        )
        raise AppError(
            401,
            "refresh_token_reused",
            "This session has been ended for security reasons. Please sign in again.",
        )

    if record.expires_at <= now:
        raise AppError(401, "refresh_token_expired", "The refresh token has expired.")

    record.revoked_at = now
    user = db.get(User, record.user_id)
    if user is None:
        raise AppError(401, "invalid_refresh_token", "The refresh token is not valid.")

    return issue_token_pair(db, user, family_id=record.family_id)


def revoke_family(db: Session, family_id: uuid.UUID) -> None:
    now = datetime.now(UTC)
    for token in db.scalars(
        select(RefreshToken).where(
            RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None)
        )
    ):
        token.revoked_at = now


def logout(db: Session, raw_token: str) -> None:
    """Revoke the presented token's whole family.

    Logout is silent on an unknown token: the caller's intent is satisfied either
    way, and reporting it would leak which tokens exist.
    """
    record = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == _hash_refresh_token(raw_token))
    )
    if record is not None:
        revoke_family(db, record.family_id)
    db.commit()


def revoke_all_for_user(db: Session, user_id: uuid.UUID) -> None:
    now = datetime.now(UTC)
    for token in db.scalars(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
        )
    ):
        token.revoked_at = now

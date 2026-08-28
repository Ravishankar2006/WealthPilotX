import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

MIN_PASSWORD_LENGTH = 12


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=128)
    # §17.1 — terms and privacy policy accepted at registration, not afterwards.
    tos_accepted: bool

    @field_validator("tos_accepted")
    @classmethod
    def _must_accept(cls, value: bool) -> bool:
        if not value:
            raise ValueError("The terms of service and privacy policy must be accepted.")
        return value

    @field_validator("password")
    @classmethod
    def _not_trivial(cls, value: str) -> str:
        if value.strip() != value:
            raise ValueError("Password must not begin or end with whitespace.")
        if len(set(value)) < 5:
            raise ValueError("Password is too repetitive.")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - RFC 6750 scheme name, not a secret
    expires_at: datetime


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    created_at: datetime


class RegisterResponse(BaseModel):
    user: UserOut
    tokens: TokenPair

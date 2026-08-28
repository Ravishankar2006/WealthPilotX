from typing import Any

from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str
    fields: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    """Documents the §13.1 envelope in the OpenAPI schema."""

    error: ErrorBody


class MessageResponse(BaseModel):
    message: str


class HealthResponse(BaseModel):
    status: str
    database: str
    environment: str

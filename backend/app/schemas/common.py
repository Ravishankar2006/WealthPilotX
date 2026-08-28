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


class IngestionJobHealth(BaseModel):
    job: str
    last_status: str
    last_run_at: str | None = None
    last_success_at: str | None = None
    stale: bool
    healthy: bool


class IngestionHealth(BaseModel):
    """FR-04's alert surface: whether ingestion is keeping up, and since when."""

    healthy: bool
    latest_market_date: str | None = None
    jobs: list[IngestionJobHealth] = []


class HealthResponse(BaseModel):
    status: str
    database: str
    environment: str
    ingestion: IngestionHealth | None = None

"""The single error shape every endpoint returns (PRD §13.1).

    {"error": {"code": str, "message": str, "fields": object | null}}

Handlers are registered globally in `app.main` so that no route can accidentally
ship a differently-shaped error. Client code parses one contract, forever.
"""

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

logger = get_logger(__name__)

_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
}


class AppError(Exception):
    """Raise this instead of HTTPException so the code and fields stay explicit."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        fields: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.fields = fields


def envelope(code: str, message: str, fields: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "fields": fields}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=envelope(exc.code, exc.message, exc.fields),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields: dict[str, list[str]] = {}
        for error in exc.errors():
            # loc is ("body", "field", ...) — drop the source segment for a clean key.
            parts = [str(p) for p in error["loc"][1:]] or [str(p) for p in error["loc"]]
            fields.setdefault(".".join(parts), []).append(error["msg"])
        return JSONResponse(
            # Literal rather than the status constant: Starlette renamed
            # HTTP_422_UNPROCESSABLE_ENTITY to ..._CONTENT, and the number is stable.
            status_code=422,
            content=envelope(
                "validation_error",
                "One or more fields are invalid.",
                fields,
            ),
        )

    # Registered on Starlette's class, not FastAPI's subclass: the router raises
    # the Starlette one for unmatched routes, and registering the narrower type
    # lets every such 404 escape the envelope.
    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "error")
        return JSONResponse(
            status_code=exc.status_code,
            content=envelope(code, str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(SQLAlchemyError)
    async def _db_error(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        # Never surface driver text — it can carry column values, and those columns
        # hold financial data (§11.2).
        logger.exception("database_error", extra={"error_type": type(exc).__name__})
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=envelope("internal_error", "A database error occurred."),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", extra={"error_type": type(exc).__name__})
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=envelope("internal_error", "An unexpected error occurred."),
        )

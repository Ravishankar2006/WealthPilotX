"""Correlation ID and request logging (PRD §16.4)."""

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import correlation_id, get_logger

logger = get_logger("app.request")

HEADER = "X-Correlation-ID"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Accept an inbound correlation ID, mint one otherwise, echo it back."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        cid = request.headers.get(HEADER) or str(uuid.uuid4())
        token = correlation_id.set(cid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            correlation_id.reset(token)

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers[HEADER] = cid

        # Query strings are omitted on purpose: they are a classic route for
        # sensitive values to reach a log sink.
        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
                "correlation_id": cid,
            },
        )
        return response

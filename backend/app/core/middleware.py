"""Correlation ID, request logging and request metrics (PRD §16.4)."""

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import correlation_id, get_logger
from app.services.metrics_service import metrics

logger = get_logger("app.request")

HEADER = "X-Correlation-ID"


def _route_template(request: Request) -> str:
    """The matched route's template, with the router prefix restored.

    `scope["route"]` is set by whichever router matched, and this FastAPI version
    keeps included routers nested rather than flattening them — so the path that
    arrives here is router-relative (`/market/{symbol}`), not the full one. Taken at
    face value, a future `/api/v2/health` would share a metrics series with
    `/api/v1/health`.

    The prefix is recovered by rendering the template back into a concrete path and
    measuring how much of the real path it accounts for. Substituting `{symbol}`
    into the raw path directly would be shorter and wrong: a symbol whose value also
    appears in the prefix would rewrite the wrong occurrence.
    """
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if not template:
        # A request that matched no route at all. Bucketed under one name rather
        # than creating a series per URL, or a scanner walking the URL space is an
        # unbounded memory leak.
        return "<unrouted>"

    rendered = template
    for name, value in (request.scope.get("path_params") or {}).items():
        rendered = rendered.replace(f"{{{name}}}", str(value))

    path = request.url.path
    prefix = path[: len(path) - len(rendered)] if path.endswith(rendered) else ""
    return f"{prefix}{template}"


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

        # The *route template*, not the path: `/api/v1/market/{symbol}` rather than
        # `/api/v1/market/AAPL`. A metrics series is designed to be scraped and
        # retained, and raw paths would put a user's chosen symbol — and, on other
        # routes, an id — into exactly that surface. An unrouted request (a 404 on
        # no route at all) has no template and is bucketed as such rather than
        # creating one series per bad URL.
        metrics.record_request(_route_template(request), response.status_code, duration_ms)

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

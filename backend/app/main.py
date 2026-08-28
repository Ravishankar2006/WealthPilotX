"""FastAPI application factory.

Everything cross-cutting is wired here — error envelope, correlation IDs,
structured logging, rate limiting — so no individual route can ship without them.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import CorrelationIdMiddleware
from app.core.ratelimit import RateLimit

API_PREFIX = "/api/v1"

DISCLAIMER = (
    "WealthPilotX is an educational and research decision-support tool. It does not "
    "provide licensed financial, investment, tax or legal advice, does not execute "
    "trades, and does not hold funds. Model outputs and past performance do not "
    "guarantee future results."
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging("DEBUG" if settings.debug else "INFO")
    get_logger(__name__).info("api_started", extra={"environment": settings.environment})
    yield


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="WealthPilotX API",
        version="0.1.0",
        description=DISCLAIMER,
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.environment != "production" else None,
        lifespan=lifespan,
    )

    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Correlation-ID"],
    )

    register_error_handlers(app)

    # §13.1 — the default allowance applies to every v1 route. Individual routes
    # narrow it further by declaring their own RateLimit dependency.
    app.include_router(
        api_router,
        prefix=API_PREFIX,
        dependencies=[Depends(RateLimit("default", settings.rate_limit_per_minute))],
    )

    @app.get("/", tags=["system"])
    def root() -> dict[str, str]:
        return {"service": "WealthPilotX API", "version": "0.1.0", "disclaimer": DISCLAIMER}

    return app


app = create_app()

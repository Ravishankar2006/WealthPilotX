"""Response security headers (§16.2).

§16.2 requires HTTPS in production. HTTPS alone does not stop a browser from
MIME-sniffing a JSON error into script, framing the API inside another origin, or
leaking a URL through a Referer header, so the headers that close those go here.

Two decisions worth stating:

**HSTS is sent only where TLS actually terminates.** `Strict-Transport-Security`
tells a browser to refuse plain HTTP for this host for the next year. Sent from a
local server on `localhost`, it does that to `localhost` — for every other project on
the machine, persistently, with no obvious way to undo it.

The condition is an **allow-list of the deployed environments** (`staging`,
`production`), not a deny-list of the local ones. That distinction is not stylistic:
the first version of this file excluded `{"development", "test"}` — and this repo's
environments are `local | test | staging | production` (`app/core/config.py`).
`development` is not one of them, so the guard matched nothing and the development
stack shipped HSTS for a year on localhost. A deny-list of environment names fails
open on the name you did not think of; an allow-list fails closed.

**The CSP is the API's, not the frontend's.** These responses are JSON; nothing
should ever load a script, style or frame from them, so the policy denies
everything. It is skipped for the interactive docs, which legitimately load Swagger
UI from a CDN and are disabled in production anyway. The React application is served
by a separate container with no production serving configuration in this repo, so it
does *not* get a CSP from here — recorded as an open finding in
`Docs/SECURITY-REVIEW.md` rather than papered over.
"""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# One year, the minimum for preload-list eligibility, and long enough that the
# header is doing real work rather than expiring between visits.
HSTS_MAX_AGE = 31_536_000

# The environments that terminate TLS. An allow-list drawn from the `environment`
# Literal in `app/core/config.py` — see the module docstring for why it is not the
# other way round.
HSTS_ENVIRONMENTS = frozenset({"staging", "production"})

API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

# Paths that render HTML and legitimately load from a CDN. Disabled in production
# (see `app.main`), so this exemption only ever applies to a development server.
DOCS_PATHS = frozenset({"/docs", "/redoc", "/openapi.json"})

BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # `no-referrer` rather than `strict-origin-when-cross-origin`: an API URL can
    # carry a symbol or an id in its path, and there is no case where a third-party
    # host needs to know which endpoint a request came from.
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, *, environment: str) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.send_hsts = environment in HSTS_ENVIRONMENTS

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)

        # `setdefault` semantics: a route that has deliberately set one of these
        # keeps its own value.
        for header, value in BASE_HEADERS.items():
            response.headers.setdefault(header, value)

        if request.url.path not in DOCS_PATHS:
            response.headers.setdefault("Content-Security-Policy", API_CSP)

        if self.send_hsts:
            response.headers.setdefault(
                "Strict-Transport-Security",
                f"max-age={HSTS_MAX_AGE}; includeSubDomains",
            )

        return response

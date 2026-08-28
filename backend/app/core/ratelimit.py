"""Fixed-window rate limiting (PRD §13.1).

Deliberately in-process for the MVP: one API instance, no Redis to operate. The
`Limiter` interface is the seam — when §16.4's horizontal scaling arrives, swap
the backend for a shared store and nothing above this module changes.
"""

import threading
import time
from collections import defaultdict

from fastapi import Request

from app.core.errors import AppError

WINDOW_SECONDS = 60


class Limiter:
    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def check(self, key: str, bucket: str, limit: int) -> int:
        """Record a hit. Returns remaining allowance; raises when over limit."""
        now = time.monotonic()
        cutoff = now - WINDOW_SECONDS
        with self._lock:
            hits = self._hits[(key, bucket)]
            hits[:] = [t for t in hits if t > cutoff]
            if len(hits) >= limit:
                retry_after = max(1, int(WINDOW_SECONDS - (now - hits[0])))
                raise AppError(
                    status_code=429,
                    code="rate_limited",
                    message=f"Rate limit exceeded. Retry in {retry_after}s.",
                )
            hits.append(now)
            return limit - len(hits)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = Limiter()


def client_key(request: Request) -> str:
    """Identify the caller: authenticated user when known, else client address."""
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return f"user:{user_id}"
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    return f"ip:{request.client.host if request.client else 'unknown'}"


class RateLimit:
    """Route dependency. `RateLimit('expensive', 10)` for the heavy endpoints."""

    def __init__(self, bucket: str, limit: int) -> None:
        self.bucket = bucket
        self.limit = limit

    def __call__(self, request: Request) -> None:
        limiter.check(client_key(request), self.bucket, self.limit)

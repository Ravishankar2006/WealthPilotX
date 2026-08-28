"""Retry with exponential backoff and jitter (FR-04 acceptance criterion 2).

FR-04 requires that a provider outage produce a retried, alertable failure rather
than a silently skipped day. Retrying is the first half of that; `app.services.
ingestion` handles the alerting half.

Jitter matters more than it looks: 32 symbols failing together and retrying on an
identical schedule is a self-inflicted thundering herd against a provider that is
already struggling — and against an unofficial API, that is how an outage becomes
a rate-limit ban.
"""

import random
import time
from collections.abc import Callable

from app.core.logging import get_logger, safe_extra
from app.providers.base import RETRYABLE, ProviderError, ProviderRateLimitedError

logger = get_logger(__name__)

DEFAULT_ATTEMPTS = 4
DEFAULT_BASE_DELAY = 1.0
DEFAULT_MAX_DELAY = 30.0
# A rate limit is the provider telling us our pace is the problem, so it gets a
# longer first backoff than a plain connection error.
RATE_LIMIT_MULTIPLIER = 4.0


def backoff_delay(
    attempt: int,
    *,
    base: float = DEFAULT_BASE_DELAY,
    maximum: float = DEFAULT_MAX_DELAY,
    multiplier: float = 1.0,
    jitter: Callable[[], float] = random.random,
) -> float:
    """Delay before `attempt` (1-based). Exponential, capped, then full-jittered.

    `jitter` is injected so the schedule is exactly assertable in tests.
    """
    raw = min(base * multiplier * (2 ** (attempt - 1)), maximum)
    return raw * jitter()


def with_retry[T](
    operation: Callable[[], T],
    *,
    description: str,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> T:
    """Run `operation`, retrying only the transient members of `ProviderError`.

    `SymbolNotFoundError` and `ProviderConfigurationError` propagate immediately: no
    number of retries will conjure a delisted ticker or a missing API key.
    """
    last: ProviderError | None = None

    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except RETRYABLE as exc:
            last = exc
            if attempt == attempts:
                break
            delay = backoff_delay(
                attempt,
                base=base_delay,
                maximum=max_delay,
                multiplier=RATE_LIMIT_MULTIPLIER
                if isinstance(exc, ProviderRateLimitedError)
                else 1.0,
                jitter=jitter,
            )
            logger.warning(
                "provider_retry",
                extra=safe_extra(
                    operation=description,
                    attempt=attempt,
                    of=attempts,
                    delay_seconds=round(delay, 3),
                    error_type=type(exc).__name__,
                ),
            )
            sleep(delay)

    assert last is not None  # only reachable after a retryable failure
    logger.error(
        "provider_exhausted",
        extra=safe_extra(operation=description, attempts=attempts, error_type=type(last).__name__),
    )
    raise last

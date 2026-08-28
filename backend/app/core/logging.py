"""Structured JSON logging with a hard redaction filter (PRD §16.4, §11.2).

Two jobs:

1. Every log line is JSON and carries the request's correlation ID, so a single
   user action can be traced across the API and, later, background jobs.
2. Financial and credential values never reach a log sink. The filter below is a
   backstop, not a licence to log carelessly — but backstops are what hold when
   someone adds a debug line at 2am.
"""

import json
import logging
import re
import sys
from contextvars import ContextVar
from typing import Any

correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

# Keys whose values must never be serialised into a log record.
SENSITIVE_KEYS = frozenset(
    {
        "income",
        "savings",
        "password",
        "password_hash",
        "token",
        "access_token",
        "refresh_token",
        "token_hash",
        "authorization",
        "jwt_secret",
        "profile_encryption_key",
    }
)

REDACTED = "[redacted]"

# Catches `income=12345`, `"savings": 400`, `password='hunter2'` in free-text messages.
_INLINE_SENSITIVE = re.compile(
    r"(?i)\b(" + "|".join(SENSITIVE_KEYS) + r")\b(\"?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;}\)]+)"
)

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


def scrub(value: Any) -> Any:
    """Recursively redact sensitive keys in dicts/lists and inline in strings."""
    if isinstance(value, dict):
        return {
            k: (REDACTED if str(k).lower() in SENSITIVE_KEYS else scrub(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        return _INLINE_SENSITIVE.sub(rf"\1\2{REDACTED}", value)
    return value


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = scrub(record.msg)
        if record.args:
            record.args = (
                scrub(dict(record.args))
                if isinstance(record.args, dict)
                else tuple(scrub(a) for a in record.args)
            )
        for key in list(record.__dict__):
            if key in _RESERVED:
                continue
            record.__dict__[key] = (
                REDACTED if key.lower() in SENSITIVE_KEYS else scrub(record.__dict__[key])
            )
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }

        cid = correlation_id.get()
        if cid:
            payload["correlation_id"] = cid

        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn ships its own handlers; route them through ours so nothing escapes
    # the redaction filter.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True


def safe_extra(**fields: Any) -> dict[str, Any]:
    """Build a logging `extra` dict that cannot collide with LogRecord internals.

    Passing a reserved name such as `created`, `module` or `name` through `extra`
    raises KeyError inside the logging module — at call time, in production, on a
    path that may only run rarely. Colliding keys are prefixed instead.
    """
    return {(f"field_{k}" if k in _RESERVED else k): v for k, v in fields.items()}


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

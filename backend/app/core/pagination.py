"""Cursor pagination (PRD §13.1).

    ?limit=&cursor=   →   {"data": [...], "next_cursor": string|null}

Cursors are opaque base64 rather than a raw value, for one reason: the moment a
client learns that the cursor is a date it will start constructing them, and the
pagination key can then never change without breaking those clients. Opaque means
the server keeps the freedom to change what it pages on.

Keyset, not offset. Ingestion inserts rows while a client is paging; with OFFSET
that shifts every subsequent page and silently skips rows.
"""

import base64
import binascii

from app.core.errors import AppError

DEFAULT_LIMIT = 50
MAX_LIMIT = 500


def encode_cursor(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> str:
    """Decode a cursor, or raise a 400 in the §13.1 envelope.

    A malformed cursor is the client's error, not a 500 — and never a silent reset
    to page one, which would loop a paging client forever.
    """
    try:
        padding = "=" * (-len(cursor) % 4)
        return base64.urlsafe_b64decode(cursor + padding).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise AppError(400, "invalid_cursor", "The pagination cursor is not valid.") from exc

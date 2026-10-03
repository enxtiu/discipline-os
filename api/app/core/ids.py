"""UUIDv7 identifiers.

The whole system uses client-generated, time-ordered UUIDv7 primary keys:

* the client can create a record while offline and keep the same id forever;
* writes are therefore idempotent (PUT/POST with an explicit id);
* the ids sort by creation time, which keeps B-tree indexes healthy.

RFC 9562 layout implemented below::

     0                   1                   2                   3
     0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    |                           unix_ts_ms                          |
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    |          unix_ts_ms           |  ver  |       rand_a          |
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    |var|                        rand_b                             |
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    |                            rand_b                             |
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import UTC, datetime

__all__ = ["coerce_id", "is_uuid", "new_id", "new_token_id", "uuid7_at", "uuid7_timestamp"]

_LOCK = threading.Lock()
_LAST_MS = 0
_COUNTER = 0
_COUNTER_BITS = 12
_COUNTER_MAX = (1 << _COUNTER_BITS) - 1


def _raw_uuid7(ts_ms: int) -> uuid.UUID:
    global _LAST_MS, _COUNTER

    with _LOCK:
        if ts_ms <= _LAST_MS:
            ts_ms = _LAST_MS
            _COUNTER += 1
            if _COUNTER > _COUNTER_MAX:  # > 4096 ids in one ms: borrow a ms
                _LAST_MS += 1
                ts_ms = _LAST_MS
                _COUNTER = 0
        else:
            _LAST_MS = ts_ms
            _COUNTER = 0
        counter = _COUNTER

    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = ((ts_ms & 0xFFFFFFFFFFFF) << 80) | (0x7 << 76) | ((counter & _COUNTER_MAX) << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)


def new_id() -> uuid.UUID:
    """Fresh, time-ordered UUIDv7."""
    return _raw_uuid7(time.time_ns() // 1_000_000)


def uuid7_at(moment: datetime) -> uuid.UUID:
    """UUIDv7 whose embedded timestamp is ``moment`` (used by seed scripts)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return _raw_uuid7(int(moment.timestamp() * 1000))


def uuid7_timestamp(value: uuid.UUID) -> datetime:
    """Extract the creation time embedded in a UUIDv7 (returns UTC)."""
    ms = value.int >> 80
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def is_uuid(value: object) -> bool:
    return isinstance(value, uuid.UUID) or (isinstance(value, str) and _parses(value) is not None)


def _parses(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


def coerce_id(value: str | uuid.UUID | None) -> uuid.UUID:
    """Client-supplied id -> :class:`uuid.UUID`.

    Clients are *encouraged* to send UUIDv7 but we accept any RFC-4122 UUID
    (older clients/tests may send v4) and reject anything else.
    """
    if value is None:
        return new_id()
    if isinstance(value, uuid.UUID):
        return value
    parsed = _parses(str(value))
    if parsed is None:
        raise ValueError(f"not a valid uuid: {value!r}")
    return parsed


def new_token_id(prefix: str = "") -> str:
    """Opaque URL-safe token id (not a UUID; used for API tokens)."""
    import secrets

    return f"{prefix}{secrets.token_urlsafe(32)}"

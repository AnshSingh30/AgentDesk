"""UUIDv7 primary keys (time-ordered) — Python 3.12 has no uuid.uuid7 yet."""

from __future__ import annotations

import os
import threading
import time
import uuid

_lock = threading.Lock()
_last_ms = 0
_seq = 0


def uuid7() -> uuid.UUID:
    """Time-ordered UUIDv7; monotonic within a millisecond via a 12-bit sequence."""
    global _last_ms, _seq
    with _lock:
        ms = int(time.time() * 1000) & ((1 << 48) - 1)
        if ms <= _last_ms:
            ms = _last_ms
            _seq = (_seq + 1) & 0xFFF
            if _seq == 0:
                ms += 1
        else:
            _seq = int.from_bytes(os.urandom(2), "big") & 0x7FF
        _last_ms = ms
        rand_a = _seq
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)

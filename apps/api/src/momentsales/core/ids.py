"""UUIDv7 identifiers (time-ordered, RFC 9562). Python 3.12 has no uuid.uuid7."""

from __future__ import annotations

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    unix_ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")
    value = (unix_ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76  # version
    value |= (rand >> 68) << 64  # 12 bits rand_a
    value |= 0b10 << 62  # variant
    value |= rand & ((1 << 62) - 1)  # 62 bits rand_b
    return uuid.UUID(int=value)

"""Token buckets per session for the chat and confirm endpoints (phase 3 hardening).

A session may start at most ``per_minute`` turns per minute, with a burst of the same size: the LLM and the
worker are shared, and one runaway client must not take them for everyone. The limiter keeps its buckets in
memory (one api process in this stack); the clock is injectable for tests.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class Bucket:
    tokens: float
    updated: float


@dataclass
class RateLimiter:
    per_minute: int
    clock: Callable[[], float] = time.monotonic
    buckets: dict[str, Bucket] = field(default_factory=dict)

    def allow(self, key: str) -> bool:
        """Take one token for ``key``; False when the bucket is empty."""
        if self.per_minute <= 0:
            return True
        now = self.clock()
        bucket = self.buckets.get(key)
        if bucket is None:
            bucket = self.buckets[key] = Bucket(tokens=float(self.per_minute), updated=now)
        refill = (now - bucket.updated) * self.per_minute / 60.0
        bucket.tokens = min(float(self.per_minute), bucket.tokens + refill)
        bucket.updated = now
        if bucket.tokens < 1.0:
            return False
        bucket.tokens -= 1.0
        return True

    def forget(self, key: str) -> None:
        self.buckets.pop(key, None)

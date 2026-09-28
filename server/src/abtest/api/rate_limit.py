"""Per-key rate limiting for event ingestion (PRD §11)."""

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(slots=True)
class _Bucket:
    tokens: float
    refilled_at: float


class TokenBucketLimiter:
    """A token bucket per key: `burst` requests at once, refilled at `rate` per second.

    In memory, so each API process counts on its own: correct for one instance. With several
    instances behind a load balancer, each would allow the full rate; the limit would then
    belong in Redis or the load balancer (ADR-015).

    Route handlers run in a thread pool, so a lock guards the buckets.
    """

    def __init__(
        self, rate: float, burst: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if rate <= 0 or burst < 1:
            raise ValueError("rate must be > 0 and burst >= 1")
        self.rate = rate
        self.burst = burst
        self._clock = clock
        self._buckets: dict[bytes, _Bucket] = {}
        self._lock = threading.Lock()

    def acquire(self, key: bytes) -> float | None:
        """Take a token for this key. Returns None if allowed, otherwise the seconds to wait
        before a token is available (for Retry-After)."""
        with self._lock:
            now = self._clock()
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = self._buckets[key] = _Bucket(tokens=self.burst, refilled_at=now)
            elapsed = now - bucket.refilled_at
            bucket.tokens = min(self.burst, bucket.tokens + elapsed * self.rate)
            bucket.refilled_at = now
            if bucket.tokens >= 1:
                bucket.tokens -= 1
                return None
            return (1 - bucket.tokens) / self.rate


def retry_after_header(wait_seconds: float) -> str:
    """Retry-After takes whole seconds; round up so a client that obeys it succeeds."""
    return str(max(1, math.ceil(wait_seconds)))

"""The token bucket, with a fake clock."""

import pytest

from abtest.api.rate_limit import TokenBucketLimiter, retry_after_header


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_allows_a_burst_then_refills_at_the_rate() -> None:
    clock = Clock()
    limiter = TokenBucketLimiter(rate=10, burst=3, clock=clock)

    assert [limiter.acquire(b"k") for _ in range(3)] == [None, None, None]
    assert limiter.acquire(b"k") == pytest.approx(0.1)  # one token takes 1/10 s
    clock.now += 0.1
    assert limiter.acquire(b"k") is None


def test_the_bucket_never_holds_more_than_the_burst() -> None:
    clock = Clock()
    limiter = TokenBucketLimiter(rate=10, burst=2, clock=clock)
    clock.now += 3600  # an hour idle

    assert [limiter.acquire(b"k") is None for _ in range(3)] == [True, True, False]


def test_each_key_has_its_own_bucket() -> None:
    limiter = TokenBucketLimiter(rate=1, burst=1, clock=Clock())

    assert limiter.acquire(b"a") is None
    assert limiter.acquire(b"a") is not None
    assert limiter.acquire(b"b") is None


@pytest.mark.parametrize(("wait", "header"), [(0.01, "1"), (1.0, "1"), (1.2, "2"), (0.0, "1")])
def test_retry_after_rounds_up_to_whole_seconds(wait: float, header: str) -> None:
    assert retry_after_header(wait) == header


def test_rejects_a_nonsensical_configuration() -> None:
    with pytest.raises(ValueError):
        TokenBucketLimiter(rate=0, burst=10)

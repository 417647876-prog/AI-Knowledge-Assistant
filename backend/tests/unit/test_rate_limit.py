from datetime import UTC, datetime, timedelta

import pytest

from app.core.rate_limit import SlidingWindowRateLimiter


def test_sliding_window_allows_limit_then_rejects_and_recovers() -> None:
    now = datetime(2026, 7, 17, tzinfo=UTC)
    limiter = SlidingWindowRateLimiter(window=timedelta(minutes=1), limit=2)

    assert limiter.allow("alice", now=now) is True
    assert limiter.allow("alice", now=now + timedelta(seconds=1)) is True
    assert limiter.allow("alice", now=now + timedelta(seconds=2)) is False
    assert limiter.allow("alice", now=now + timedelta(seconds=61)) is True


def test_limiter_never_keeps_plaintext_key() -> None:
    limiter = SlidingWindowRateLimiter(window=timedelta(minutes=1), limit=1)
    limiter.allow("alice@example.test", now=datetime(2026, 7, 17, tzinfo=UTC))

    assert all("alice" not in key for key in limiter._windows)  # noqa: SLF001
    assert all(len(key) == 64 for key in limiter._windows)  # noqa: SLF001


def test_limiter_rejects_invalid_constructor_boundaries() -> None:
    with pytest.raises(ValueError):
        SlidingWindowRateLimiter(window=timedelta(), limit=1)
    with pytest.raises(ValueError):
        SlidingWindowRateLimiter(window=timedelta(seconds=1), limit=0)

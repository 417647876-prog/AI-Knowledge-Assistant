"""单进程滑动窗口限速器，不保留可识别的原始键。"""

import hashlib
from collections import deque
from datetime import UTC, datetime, timedelta


class SlidingWindowRateLimiter:
    def __init__(self, *, window: timedelta, limit: int) -> None:
        if window <= timedelta(0):
            raise ValueError("window 必须大于零")
        if limit < 1:
            raise ValueError("limit 必须至少为一")
        self._window = window
        self._limit = limit
        self._windows: dict[str, deque[datetime]] = {}

    def allow(self, key: str, *, now: datetime | None = None) -> bool:
        moment = now or datetime.now(UTC)
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("now 必须包含时区")
        hashed_key = hashlib.sha256(key.encode("utf-8")).hexdigest()
        timestamps = self._windows.setdefault(hashed_key, deque())
        boundary = moment - self._window
        while timestamps and timestamps[0] <= boundary:
            timestamps.popleft()
        if len(timestamps) >= self._limit:
            return False
        timestamps.append(moment)
        return True

    def clear(self, key: str) -> None:
        self._windows.pop(hashlib.sha256(key.encode("utf-8")).hexdigest(), None)

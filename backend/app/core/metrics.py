from collections import Counter
from threading import Lock

_LATENCY_BUCKETS = (10, 50, 100, 500, 1000)


class MetricsRegistry:
    """In-process numeric request counters; it intentionally has no user-input labels."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._request_count = 0
        self._error_count = 0
        self._sse_request_count = 0
        self._latencies: Counter[int] = Counter()

    def record_api_request(self, *, status_code: int, duration_ms: float, is_sse: bool) -> None:
        with self._lock:
            self._request_count += 1
            self._error_count += int(status_code >= 400)
            self._sse_request_count += int(is_sse)
            for bucket in _LATENCY_BUCKETS:
                if duration_ms <= bucket:
                    self._latencies[bucket] += 1

    def reset(self) -> None:
        with self._lock:
            self._request_count = 0
            self._error_count = 0
            self._sse_request_count = 0
            self._latencies.clear()

    def api_snapshot(self) -> dict[str, object]:
        with self._lock:
            request_count = self._request_count
            return {
                "request_count": request_count,
                "error_count": self._error_count,
                "error_rate": self._error_count / request_count if request_count else 0.0,
                "sse_request_count": self._sse_request_count,
                "latency_buckets": {
                    **{f"le_{bucket}ms": self._latencies[bucket] for bucket in _LATENCY_BUCKETS},
                    "gt_1000ms": request_count - self._latencies[1000],
                },
            }


metrics_registry = MetricsRegistry()

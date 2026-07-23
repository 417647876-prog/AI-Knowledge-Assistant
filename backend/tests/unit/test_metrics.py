from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.api.middleware import MetricsMiddleware
from app.core.metrics import MetricsRegistry, metrics_registry


def test_request_metrics_count_statuses_sse_and_latency_buckets() -> None:
    metrics = MetricsRegistry()

    metrics.record_api_request(status_code=200, duration_ms=8, is_sse=False)
    metrics.record_api_request(status_code=404, duration_ms=55, is_sse=False)
    metrics.record_api_request(status_code=503, duration_ms=800, is_sse=True)

    snapshot = metrics.api_snapshot()
    assert snapshot == {
        "request_count": 3,
        "error_count": 2,
        "error_rate": 2 / 3,
        "sse_request_count": 1,
        "latency_buckets": {
            "le_10ms": 1,
            "le_50ms": 1,
            "le_100ms": 2,
            "le_500ms": 2,
            "le_1000ms": 3,
            "gt_1000ms": 0,
        },
    }


def test_metrics_never_accept_user_controlled_labels() -> None:
    metrics = MetricsRegistry()

    metrics.record_api_request(status_code=200, duration_ms=10, is_sse=False)

    assert set(metrics.api_snapshot()) == {
        "request_count",
        "error_count",
        "error_rate",
        "sse_request_count",
        "latency_buckets",
    }


def test_metrics_middleware_records_2xx_4xx_5xx_and_sse_with_fixed_clock(monkeypatch) -> None:
    app = FastAPI()

    @app.get("/ok")
    async def ok() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/missing")
    async def missing() -> None:
        raise HTTPException(status_code=404)

    @app.get("/failure")
    async def failure() -> None:
        raise RuntimeError("expected test failure")

    @app.get("/events")
    async def events() -> StreamingResponse:
        async def body():
            yield b"event: complete\ndata: {}\n\n"

        return StreamingResponse(body(), media_type="text/event-stream")

    app.add_middleware(MetricsMiddleware)
    ticks = iter((0.0, 0.008, 1.0, 1.055, 2.0, 2.8, 3.0, 4.02))
    monkeypatch.setattr("app.api.middleware.perf_counter", lambda: next(ticks))
    metrics_registry.reset()
    client = TestClient(app, raise_server_exceptions=False)

    assert client.get("/ok").status_code == 200
    assert client.get("/missing").status_code == 404
    assert client.get("/failure").status_code == 500
    assert client.get("/events").status_code == 200

    assert metrics_registry.api_snapshot() == {
        "request_count": 4,
        "error_count": 2,
        "error_rate": 0.5,
        "sse_request_count": 1,
        "latency_buckets": {
            "le_10ms": 1,
            "le_50ms": 1,
            "le_100ms": 2,
            "le_500ms": 2,
            "le_1000ms": 3,
            "gt_1000ms": 1,
        },
    }

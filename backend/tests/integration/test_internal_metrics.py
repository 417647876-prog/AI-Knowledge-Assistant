import os
from types import SimpleNamespace

import httpx
import pytest
from starlette.requests import Request

from app.api.v1.internal_metrics import require_internal_metrics_access
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.main import create_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="设置 RUN_DATABASE_TESTS=1 后运行 PostgreSQL 集成测试",
    ),
]


def _request(*, key: str = "", via_gateway: bool = False) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/internal/metrics",
            "headers": [(b"x-internal-metrics-key", key.encode())] if key else [],
        }
    )
    request._state = SimpleNamespace(via_gateway=via_gateway)  # noqa: SLF001
    return request


@pytest.mark.parametrize(
    ("key", "via_gateway"),
    [("", False), ("wrong", False), ("metrics-secret", True)],
)
def test_internal_metrics_rejects_users_missing_key_and_gateway_path(
    key: str, via_gateway: bool
) -> None:
    with pytest.raises(AppError) as error:
        require_internal_metrics_access(
            _request(key=key, via_gateway=via_gateway),
            Settings(_env_file=None, internal_metrics_key="metrics-secret"),
        )

    assert error.value.code == "INTERNAL_METRICS_NOT_FOUND"
    assert error.value.status_code == 404


def test_internal_metrics_accepts_only_the_internal_key_on_direct_path() -> None:
    require_internal_metrics_access(
        _request(key="metrics-secret"),
        Settings(_env_file=None, internal_metrics_key="metrics-secret"),
    )


@pytest.mark.asyncio
async def test_internal_metrics_endpoint_returns_numeric_and_database_summaries() -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, internal_metrics_key="metrics-secret"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Internal-Metrics-Key": "metrics-secret"},
    ) as client:
        response = await client.get("/internal/metrics")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"api", "jobs", "job_processing", "workers", "model_calls"}
    assert set(body["api"]) == {
        "request_count",
        "error_count",
        "error_rate",
        "sse_request_count",
        "latency_buckets",
    }
    assert set(body["workers"]) == {"status_counts", "latest_seen_epoch_ms"}
    assert body["workers"]["latest_seen_epoch_ms"] is None or isinstance(
        body["workers"]["latest_seen_epoch_ms"], int
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("gateway_secret", [None, "wrong-secret"])
async def test_gateway_peer_cannot_bypass_internal_metrics_with_public_path(
    monkeypatch: pytest.MonkeyPatch, gateway_secret: str | None
) -> None:
    settings = Settings(
        _env_file=None,
        internal_metrics_key="metrics-secret",
        trusted_gateway_networks=("10.0.0.0/8",),
        gateway_shared_secret="gateway-secret",
    )
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    headers = {"X-Internal-Metrics-Key": "metrics-secret"}
    if gateway_secret is not None:
        headers["X-Gateway-Secret"] = gateway_secret
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("10.1.2.3", 12345)),
        base_url="http://gateway-public-path",
        headers=headers,
    ) as client:
        response = await client.get("/internal/metrics")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_direct_internal_peer_with_metrics_key_remains_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        internal_metrics_key="metrics-secret",
        trusted_gateway_networks=("10.0.0.0/8",),
        gateway_shared_secret="gateway-secret",
    )
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("172.20.0.2", 12345)),
        base_url="http://backend-internal",
        headers={"X-Internal-Metrics-Key": "metrics-secret"},
    ) as client:
        response = await client.get("/internal/metrics")

    assert response.status_code == 200

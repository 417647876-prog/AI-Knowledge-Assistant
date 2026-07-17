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
    assert set(body["workers"]) == {"status_counts", "latest_seen_at"}

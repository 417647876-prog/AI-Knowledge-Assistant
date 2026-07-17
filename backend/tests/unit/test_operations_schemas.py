from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.api.auth_dependencies import require_admin
from app.db.session import get_session
from app.main import create_app
from app.operations.service import JobCursor, JobListQuery, OperationsTimeRange


def test_operations_time_range_requires_timezone_aware_iso_datetimes() -> None:
    with pytest.raises(ValidationError, match="时区"):
        OperationsTimeRange(start_at=datetime(2026, 7, 1, 8, 0, 0))


def test_operations_time_range_rejects_reversed_boundaries() -> None:
    with pytest.raises(ValidationError, match="开始时间"):
        OperationsTimeRange(
            start_at=datetime(2026, 7, 2, tzinfo=UTC),
            end_at=datetime(2026, 7, 1, tzinfo=UTC),
        )


def test_job_list_query_has_bounded_default_page_and_stable_cursor() -> None:
    query = JobListQuery()
    assert query.limit == 20

    cursor = JobCursor(created_at=datetime(2026, 7, 1, tzinfo=UTC), id=uuid4())
    with_cursor = JobListQuery(cursor_created_at=cursor.created_at, cursor_id=cursor.id)
    assert with_cursor.cursor() == cursor

    with pytest.raises(ValidationError):
        JobListQuery(limit=101)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["start_at", "end_at"])
async def test_operations_route_rejects_numeric_unix_timestamps_without_database(
    field: str,
) -> None:
    app = create_app()
    app.dependency_overrides[require_admin] = lambda: object()
    app.dependency_overrides[get_session] = lambda: object()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/v1/admin/operations/overview", params={field: "0"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422

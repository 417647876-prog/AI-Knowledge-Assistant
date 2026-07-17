from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import require_admin
from app.db.models import User
from app.db.session import get_session
from app.operations.service import (
    JobListQuery,
    JobsResponse,
    OperationsTimeRange,
    OverviewResponse,
    QualityResponse,
    UserOperationsSummary,
    get_jobs,
    get_overview,
    get_quality,
    get_users,
)

router = APIRouter(prefix="/api/v1/admin/operations", tags=["admin-operations"])


def _validation_error(error: ValidationError) -> RequestValidationError:
    return RequestValidationError(errors=error.errors())


def _parse_iso_datetime(value: str | None, field: str) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise RequestValidationError(
            errors=[
                {
                    "type": "value_error",
                    "loc": ("query", field),
                    "msg": "时间必须是 ISO 8601 格式",
                    "input": value,
                }
            ]
        ) from error


def time_range_query(
    start_at: Annotated[str | None, Query()] = None,
    end_at: Annotated[str | None, Query()] = None,
) -> OperationsTimeRange:
    try:
        return OperationsTimeRange(
            start_at=_parse_iso_datetime(start_at, "start_at"),
            end_at=_parse_iso_datetime(end_at, "end_at"),
        )
    except ValidationError as error:
        raise _validation_error(error) from error


def jobs_query(
    start_at: Annotated[str | None, Query()] = None,
    end_at: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor_created_at: Annotated[datetime | None, Query()] = None,
    cursor_id: Annotated[UUID | None, Query()] = None,
) -> JobListQuery:
    try:
        return JobListQuery(
            start_at=_parse_iso_datetime(start_at, "start_at"),
            end_at=_parse_iso_datetime(end_at, "end_at"),
            limit=limit,
            cursor_created_at=cursor_created_at,
            cursor_id=cursor_id,
        )
    except ValidationError as error:
        raise _validation_error(error) from error


@router.get("/overview", response_model=OverviewResponse)
async def overview(
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    time_range: Annotated[OperationsTimeRange, Depends(time_range_query)],
) -> OverviewResponse:
    return await get_overview(session, time_range)


@router.get("/users", response_model=list[UserOperationsSummary])
async def users(
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    time_range: Annotated[OperationsTimeRange, Depends(time_range_query)],
) -> list[UserOperationsSummary]:
    return await get_users(session, time_range)


@router.get("/jobs", response_model=JobsResponse)
async def jobs(
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    query: Annotated[JobListQuery, Depends(jobs_query)],
) -> JobsResponse:
    return await get_jobs(session, query)


@router.get("/quality", response_model=QualityResponse)
async def quality(
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    time_range: Annotated[OperationsTimeRange, Depends(time_range_query)],
) -> QualityResponse:
    return await get_quality(session, time_range)

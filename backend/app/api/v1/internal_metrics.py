import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.core.metrics import metrics_registry
from app.db.models import DocumentJob, LlmUsageEvent, WorkerHeartbeat
from app.db.session import get_session

router = APIRouter(prefix="/internal", tags=["internal"])


def require_internal_metrics_access(request: Request, settings: Settings) -> None:
    supplied_key = request.headers.get("X-Internal-Metrics-Key", "")
    if (
        not settings.internal_metrics_key
        or not hmac.compare_digest(supplied_key, settings.internal_metrics_key)
        or getattr(request.state, "via_gateway", False)
    ):
        raise AppError(code="INTERNAL_METRICS_NOT_FOUND", message="资源不存在。", status_code=404)


@router.get("/metrics")
async def internal_metrics(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    require_internal_metrics_access(request, settings)
    return {
        "api": metrics_registry.api_snapshot(),
        "jobs": await _status_counts(session, DocumentJob.status),
        "job_processing": await _duration_summary(
            session, DocumentJob.started_at, DocumentJob.finished_at
        ),
        "workers": await _heartbeat_summary(session),
        "model_calls": await _model_call_summary(session),
    }


async def _status_counts(session: AsyncSession, column: Any) -> dict[str, int]:
    rows = await session.execute(select(column, func.count()).group_by(column))
    return {str(status): int(count) for status, count in rows}


async def _duration_summary(
    session: AsyncSession, started_at: Any, finished_at: Any
) -> dict[str, float | int]:
    duration = func.extract("epoch", finished_at - started_at) * 1000
    count, total, average = (
        await session.execute(
            select(
                func.count(duration),
                func.coalesce(func.sum(duration), 0),
                func.coalesce(func.avg(duration), 0),
            ).where(started_at.is_not(None), finished_at.is_not(None))
        )
    ).one()
    return {"count": int(count), "total_ms": float(total), "average_ms": float(average)}


async def _heartbeat_summary(session: AsyncSession) -> dict[str, Any]:
    status_counts = await _status_counts(session, WorkerHeartbeat.status)
    latest_seen = await session.scalar(select(func.max(WorkerHeartbeat.last_seen_at)))
    return {
        "status_counts": status_counts,
        "latest_seen_epoch_ms": int(latest_seen.timestamp() * 1000) if latest_seen else None,
    }


async def _model_call_summary(session: AsyncSession) -> dict[str, float | int]:
    count, total, average = (
        await session.execute(
            select(
                func.count(LlmUsageEvent.duration_ms),
                func.coalesce(func.sum(LlmUsageEvent.duration_ms), 0),
                func.coalesce(func.avg(LlmUsageEvent.duration_ms), 0),
            ).where(LlmUsageEvent.duration_ms.is_not(None))
        )
    ).one()
    return {"count": int(count), "total_ms": float(total), "average_ms": float(average)}

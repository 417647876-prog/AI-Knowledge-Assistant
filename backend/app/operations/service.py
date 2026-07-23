from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AnswerFeedback,
    AnswerObservation,
    AuditEvent,
    Document,
    DocumentJob,
    KnowledgeBase,
    LlmUsageEvent,
    QualityEvaluationRun,
    User,
    WorkerHeartbeat,
)


class OperationsTimeRange(BaseModel):
    """运营查询时间范围；只接受带时区的 ISO 8601 时间。"""

    model_config = ConfigDict(extra="forbid")

    start_at: datetime | None = None
    end_at: datetime | None = None

    @field_validator("start_at", "end_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("时间必须携带时区")
        return value

    @model_validator(mode="after")
    def validate_boundaries(self) -> "OperationsTimeRange":
        if self.start_at is not None and self.end_at is not None and self.start_at > self.end_at:
            raise ValueError("开始时间不能晚于结束时间")
        return self


class JobCursor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created_at: datetime
    id: UUID

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("时间必须携带时区")
        return value


class JobListQuery(OperationsTimeRange):
    limit: int = Field(default=20, ge=1, le=100)
    cursor_created_at: datetime | None = None
    cursor_id: UUID | None = None

    @field_validator("cursor_created_at")
    @classmethod
    def require_cursor_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("时间必须携带时区")
        return value

    @model_validator(mode="after")
    def validate_cursor(self) -> "JobListQuery":
        if (self.cursor_created_at is None) != (self.cursor_id is None):
            raise ValueError("游标必须同时包含时间和 UUID")
        return self

    def cursor(self) -> JobCursor | None:
        if self.cursor_created_at is None or self.cursor_id is None:
            return None
        return JobCursor(created_at=self.cursor_created_at, id=self.cursor_id)


class JobItem(BaseModel):
    id: UUID
    resource_type: str
    status: str
    stage: str | None
    attempt_count: int
    error_code: str | None
    created_at: datetime


class JobsResponse(BaseModel):
    items: list[JobItem]
    next_cursor: JobCursor | None


class OverviewResponse(BaseModel):
    account_total: int
    active_account_total: int
    knowledge_base_total: int
    document_total: int
    effective_document_bytes: int
    job_status_counts: dict[str, int]
    token_total: int
    cost_total: Decimal
    feedback: dict[str, int]
    risk_event_total: int
    system_health: dict[str, Any]


class UserOperationsSummary(BaseModel):
    user_id: UUID
    username: str
    role: str
    is_active: bool
    knowledge_base_total: int
    document_total: int
    effective_document_bytes: int
    job_total: int
    token_total: int
    cost_total: Decimal


class OfflineEvaluationSummary(BaseModel):
    mode: str
    gate_passed: bool
    started_at: datetime
    completed_at: datetime
    duration_ms: int


class QualityResponse(BaseModel):
    latest_offline_evaluation: OfflineEvaluationSummary | None
    online_agent_metrics: dict[str, int | float]
    feedback_distribution: dict[str, int]


def _range_conditions(column: Any, time_range: OperationsTimeRange) -> list[Any]:
    conditions: list[Any] = []
    if time_range.start_at is not None:
        conditions.append(column >= time_range.start_at)
    if time_range.end_at is not None:
        conditions.append(column <= time_range.end_at)
    return conditions


async def get_overview(session: AsyncSession, time_range: OperationsTimeRange) -> OverviewResponse:
    account_total, active_account_total = (
        await session.execute(
            select(
                func.count(User.id),
                func.count(User.id).filter(User.is_active.is_(True)),
            ).where(*_range_conditions(User.created_at, time_range))
        )
    ).one()
    knowledge_base_total = await session.scalar(
        select(func.count(KnowledgeBase.id)).where(
            *_range_conditions(KnowledgeBase.created_at, time_range)
        )
    )
    document_total, effective_document_bytes = (
        await session.execute(
            select(
                func.count(Document.id),
                func.coalesce(func.sum(Document.file_size), 0),
            ).where(
                Document.deleted_at.is_(None),
                *_range_conditions(Document.created_at, time_range),
            )
        )
    ).one()
    job_status_rows = await session.execute(
        select(DocumentJob.status, func.count(DocumentJob.id))
        .where(*_range_conditions(DocumentJob.created_at, time_range))
        .group_by(DocumentJob.status)
    )
    token_total, cost_total = (
        await session.execute(
            select(
                func.coalesce(func.sum(LlmUsageEvent.total_tokens), 0),
                func.coalesce(func.sum(LlmUsageEvent.settled_cost), 0),
            ).where(*_range_conditions(LlmUsageEvent.created_at, time_range))
        )
    ).one()
    helpful, unhelpful = (
        await session.execute(
            select(
                func.count(AnswerFeedback.id).filter(AnswerFeedback.helpful.is_(True)),
                func.count(AnswerFeedback.id).filter(AnswerFeedback.helpful.is_(False)),
            ).where(*_range_conditions(AnswerFeedback.created_at, time_range))
        )
    ).one()
    risk_event_total = await session.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.result != "success",
            *_range_conditions(AuditEvent.created_at, time_range),
        )
    )
    worker_rows = await session.execute(
        select(WorkerHeartbeat.status, func.count(WorkerHeartbeat.worker_id)).group_by(
            WorkerHeartbeat.status
        )
    )
    latest_worker_seen_at = await session.scalar(select(func.max(WorkerHeartbeat.last_seen_at)))

    return OverviewResponse(
        account_total=int(account_total),
        active_account_total=int(active_account_total),
        knowledge_base_total=int(knowledge_base_total or 0),
        document_total=int(document_total),
        effective_document_bytes=int(effective_document_bytes),
        job_status_counts={str(status): int(count) for status, count in job_status_rows},
        token_total=int(token_total),
        cost_total=Decimal(cost_total),
        feedback={"helpful": int(helpful), "unhelpful": int(unhelpful)},
        risk_event_total=int(risk_event_total or 0),
        system_health={
            "worker_status_counts": {str(status): int(count) for status, count in worker_rows},
            "latest_worker_seen_at": latest_worker_seen_at,
        },
    )


async def get_users(
    session: AsyncSession, time_range: OperationsTimeRange
) -> list[UserOperationsSummary]:
    knowledge_bases = (
        select(
            KnowledgeBase.owner_id.label("user_id"),
            func.count(KnowledgeBase.id).label("knowledge_base_total"),
        )
        .where(*_range_conditions(KnowledgeBase.created_at, time_range))
        .group_by(KnowledgeBase.owner_id)
        .subquery()
    )
    documents = (
        select(
            KnowledgeBase.owner_id.label("user_id"),
            func.count(Document.id).label("document_total"),
            func.coalesce(func.sum(Document.file_size), 0).label("effective_document_bytes"),
        )
        .join(KnowledgeBase, KnowledgeBase.id == Document.knowledge_base_id)
        .where(
            Document.deleted_at.is_(None),
            *_range_conditions(Document.created_at, time_range),
        )
        .group_by(KnowledgeBase.owner_id)
        .subquery()
    )
    jobs = (
        select(
            DocumentJob.owner_user_id.label("user_id"),
            func.count(DocumentJob.id).label("job_total"),
        )
        .where(*_range_conditions(DocumentJob.created_at, time_range))
        .group_by(DocumentJob.owner_user_id)
        .subquery()
    )
    usage = (
        select(
            LlmUsageEvent.user_id.label("user_id"),
            func.coalesce(func.sum(LlmUsageEvent.total_tokens), 0).label("token_total"),
            func.coalesce(func.sum(LlmUsageEvent.settled_cost), 0).label("cost_total"),
        )
        .where(*_range_conditions(LlmUsageEvent.created_at, time_range))
        .group_by(LlmUsageEvent.user_id)
        .subquery()
    )
    rows = await session.execute(
        select(
            User.id,
            User.username,
            User.role,
            User.is_active,
            func.coalesce(knowledge_bases.c.knowledge_base_total, 0),
            func.coalesce(documents.c.document_total, 0),
            func.coalesce(documents.c.effective_document_bytes, 0),
            func.coalesce(jobs.c.job_total, 0),
            func.coalesce(usage.c.token_total, 0),
            func.coalesce(usage.c.cost_total, 0),
        )
        .outerjoin(knowledge_bases, knowledge_bases.c.user_id == User.id)
        .outerjoin(documents, documents.c.user_id == User.id)
        .outerjoin(jobs, jobs.c.user_id == User.id)
        .outerjoin(usage, usage.c.user_id == User.id)
        .where(*_range_conditions(User.created_at, time_range))
        .order_by(User.username, User.id)
    )
    return [
        UserOperationsSummary(
            user_id=user_id,
            username=username,
            role=role,
            is_active=is_active,
            knowledge_base_total=int(knowledge_base_total),
            document_total=int(document_total),
            effective_document_bytes=int(effective_document_bytes),
            job_total=int(job_total),
            token_total=int(token_total),
            cost_total=Decimal(cost_total),
        )
        for (
            user_id,
            username,
            role,
            is_active,
            knowledge_base_total,
            document_total,
            effective_document_bytes,
            job_total,
            token_total,
            cost_total,
        ) in rows
    ]


async def get_jobs(session: AsyncSession, query: JobListQuery) -> JobsResponse:
    conditions = _range_conditions(DocumentJob.created_at, query)
    cursor = query.cursor()
    if cursor is not None:
        conditions.append(
            or_(
                DocumentJob.created_at < cursor.created_at,
                and_(DocumentJob.created_at == cursor.created_at, DocumentJob.id < cursor.id),
            )
        )
    rows = list(
        await session.execute(
            select(
                DocumentJob.id,
                DocumentJob.resource_type,
                DocumentJob.status,
                DocumentJob.stage,
                DocumentJob.attempt_count,
                DocumentJob.error_code,
                DocumentJob.created_at,
            )
            .where(*conditions)
            .order_by(DocumentJob.created_at.desc(), DocumentJob.id.desc())
            .limit(query.limit + 1)
        )
    )
    page_rows = rows[: query.limit]
    items = [
        JobItem(
            id=job_id,
            resource_type=resource_type,
            status=status,
            stage=stage,
            attempt_count=attempt_count,
            error_code=error_code,
            created_at=created_at,
        )
        for job_id, resource_type, status, stage, attempt_count, error_code, created_at in page_rows
    ]
    next_cursor = (
        JobCursor(created_at=items[-1].created_at, id=items[-1].id)
        if len(rows) > query.limit and items
        else None
    )
    return JobsResponse(items=items, next_cursor=next_cursor)


async def get_quality(session: AsyncSession, time_range: OperationsTimeRange) -> QualityResponse:
    latest_evaluation_row = (
        await session.execute(
            select(
                QualityEvaluationRun.mode,
                QualityEvaluationRun.gate_passed,
                QualityEvaluationRun.started_at,
                QualityEvaluationRun.completed_at,
                QualityEvaluationRun.duration_ms,
            )
            .where(*_range_conditions(QualityEvaluationRun.completed_at, time_range))
            .order_by(QualityEvaluationRun.completed_at.desc(), QualityEvaluationRun.id.desc())
            .limit(1)
        )
    ).one_or_none()
    observation_count, refused_count, valid_citation_count, total_ms = (
        await session.execute(
            select(
                func.count(AnswerObservation.id),
                func.count(AnswerObservation.id).filter(AnswerObservation.refused.is_(True)),
                func.count(AnswerObservation.id).filter(
                    AnswerObservation.citations_valid.is_(True)
                ),
                func.coalesce(func.sum(AnswerObservation.total_ms), 0),
            ).where(*_range_conditions(AnswerObservation.created_at, time_range))
        )
    ).one()
    helpful, unhelpful = (
        await session.execute(
            select(
                func.count(AnswerFeedback.id).filter(AnswerFeedback.helpful.is_(True)),
                func.count(AnswerFeedback.id).filter(AnswerFeedback.helpful.is_(False)),
            ).where(*_range_conditions(AnswerFeedback.created_at, time_range))
        )
    ).one()
    latest_evaluation = (
        OfflineEvaluationSummary(
            mode=latest_evaluation_row.mode,
            gate_passed=latest_evaluation_row.gate_passed,
            started_at=latest_evaluation_row.started_at,
            completed_at=latest_evaluation_row.completed_at,
            duration_ms=latest_evaluation_row.duration_ms,
        )
        if latest_evaluation_row is not None
        else None
    )
    return QualityResponse(
        latest_offline_evaluation=latest_evaluation,
        online_agent_metrics={
            "observation_total": int(observation_count),
            "refused_total": int(refused_count),
            "valid_citation_total": int(valid_citation_count),
            "total_duration_ms": int(total_ms),
        },
        feedback_distribution={"helpful": int(helpful), "unhelpful": int(unhelpful)},
    )

from datetime import datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.document_job import DocumentJob
from app.db.models.worker_heartbeat import WorkerHeartbeat
from app.jobs.contracts import JobLease, JobStatus, JobType
from app.jobs.service import failure_transition, sanitize_failure, should_retry_failure

CLAIM_CANDIDATE_SQL = """
SELECT id
FROM document_jobs
WHERE (status = 'pending' AND run_after <= :now)
   OR (status = 'retry_wait' AND run_after <= :now)
   OR (status = 'processing' AND lease_expires_at < :now)
ORDER BY run_after, created_at, id
FOR UPDATE SKIP LOCKED
LIMIT 1
"""


class LeaseLostError(RuntimeError):
    pass


async def enqueue_job(
    session: AsyncSession,
    *,
    job_type: JobType,
    resource_type: str,
    resource_id: UUID,
    owner_user_id: UUID,
    knowledge_base_id: UUID,
    max_attempts: int = 3,
    run_after: datetime | None = None,
) -> DocumentJob:
    values: dict[str, object] = {
        "job_type": job_type,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "owner_user_id": owner_user_id,
        "knowledge_base_id": knowledge_base_id,
        "max_attempts": max_attempts,
    }
    if run_after is not None:
        values["run_after"] = run_after
    job = DocumentJob(**values)
    session.add(job)
    await session.flush()
    return job


async def claim_next_job(
    session: AsyncSession,
    *,
    worker_id: str,
    now: datetime,
    lease_seconds: int,
) -> JobLease | None:
    job_id = await session.scalar(text(CLAIM_CANDIDATE_SQL), {"now": now})
    if job_id is None:
        return None

    lease_token = uuid4()
    lease_expires_at = now + timedelta(seconds=lease_seconds)
    row = (
        (
            await session.execute(
                text(
                    "UPDATE document_jobs SET status='processing', lease_owner=:worker_id, "
                    "lease_token=:lease_token, lease_expires_at=:lease_expires_at, "
                    "heartbeat_at=:now, attempt_count=attempt_count + 1, started_at=:now, "
                    "finished_at=NULL, error_code=NULL, error_message=NULL "
                    "WHERE id=:job_id RETURNING id, job_type, resource_type, resource_id, "
                    "owner_user_id, knowledge_base_id, attempt_count"
                ),
                {
                    "job_id": job_id,
                    "worker_id": worker_id,
                    "lease_token": lease_token,
                    "lease_expires_at": lease_expires_at,
                    "now": now,
                },
            )
        )
        .mappings()
        .one()
    )
    return JobLease(
        job_id=row["id"],
        job_type=cast(JobType, row["job_type"]),
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        owner_user_id=row["owner_user_id"],
        knowledge_base_id=row["knowledge_base_id"],
        attempt_number=row["attempt_count"],
        lease_token=lease_token,
        lease_expires_at=lease_expires_at,
    )


async def renew_lease(
    session: AsyncSession,
    *,
    job_id: UUID,
    lease_token: UUID,
    now: datetime,
    lease_seconds: int,
) -> bool:
    result = await session.execute(
        update(DocumentJob)
        .where(
            DocumentJob.id == job_id,
            DocumentJob.status == "processing",
            DocumentJob.lease_token == lease_token,
            DocumentJob.lease_expires_at >= now,
        )
        .values(
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            heartbeat_at=now,
        )
    )
    return result.rowcount == 1


async def update_job_stage(
    session: AsyncSession,
    *,
    job_id: UUID,
    lease_token: UUID,
    stage: str,
    now: datetime,
) -> bool:
    result = await session.execute(
        update(DocumentJob)
        .where(
            DocumentJob.id == job_id,
            DocumentJob.status == "processing",
            DocumentJob.lease_token == lease_token,
            DocumentJob.lease_expires_at >= now,
        )
        .values(stage=stage, heartbeat_at=now)
    )
    return result.rowcount == 1


async def complete_job(
    session: AsyncSession,
    *,
    job_id: UUID,
    lease_token: UUID,
    chunk_count: int,
    now: datetime,
) -> bool:
    result = await session.execute(
        update(DocumentJob)
        .where(
            DocumentJob.id == job_id,
            DocumentJob.status == "processing",
            DocumentJob.lease_token == lease_token,
            DocumentJob.lease_expires_at >= now,
        )
        .values(
            status="succeeded",
            chunk_count=chunk_count,
            finished_at=now,
            heartbeat_at=now,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            error_code=None,
            error_message=None,
        )
    )
    return result.rowcount == 1


async def fail_job(
    session: AsyncSession,
    *,
    job_id: UUID,
    lease_token: UUID,
    code: str,
    message: str,
    retryable: bool,
    now: datetime,
) -> JobStatus:
    attempt = (
        await session.execute(
            select(DocumentJob.attempt_count, DocumentJob.max_attempts)
            .where(
                DocumentJob.id == job_id,
                DocumentJob.status == "processing",
                DocumentJob.lease_token == lease_token,
                DocumentJob.lease_expires_at >= now,
            )
            .with_for_update()
        )
    ).one_or_none()
    if attempt is None:
        raise LeaseLostError("任务租约已失效")

    safe_code, safe_message = sanitize_failure(code, message)
    transition = failure_transition(
        attempt_number=attempt.attempt_count,
        max_attempts=attempt.max_attempts,
        retryable=should_retry_failure(safe_code, requested=retryable),
        now=now,
    )
    result = await session.execute(
        update(DocumentJob)
        .where(
            DocumentJob.id == job_id,
            DocumentJob.status == "processing",
            DocumentJob.lease_token == lease_token,
            DocumentJob.lease_expires_at >= now,
        )
        .values(
            status=transition.status,
            run_after=transition.run_after,
            finished_at=now if transition.status == "failed" else None,
            heartbeat_at=now,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            error_code=safe_code,
            error_message=safe_message,
        )
    )
    if result.rowcount != 1:
        raise LeaseLostError("任务租约已失效")
    return transition.status


async def cancel_jobs_for_resource(
    session: AsyncSession,
    *,
    resource_type: str,
    resource_id: UUID,
    now: datetime,
) -> int:
    result = await session.execute(
        update(DocumentJob)
        .where(
            DocumentJob.resource_type == resource_type,
            DocumentJob.resource_id == resource_id,
            DocumentJob.status.in_(("pending", "processing", "retry_wait")),
        )
        .values(
            status="canceled",
            finished_at=now,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
        )
    )
    return result.rowcount


async def record_worker_heartbeat(
    session: AsyncSession,
    *,
    worker_id: str,
    status: str,
    current_job_id: UUID | None,
    now: datetime,
) -> None:
    statement = insert(WorkerHeartbeat).values(
        worker_id=worker_id,
        status=status,
        current_job_id=current_job_id,
        last_seen_at=now,
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[WorkerHeartbeat.worker_id],
            set_={
                "status": status,
                "current_job_id": current_job_id,
                "last_seen_at": now,
            },
        )
    )


async def worker_heartbeat_is_fresh(
    session: AsyncSession,
    *,
    worker_id: str,
    now: datetime,
    max_age_seconds: int,
) -> bool:
    last_seen_at = await session.scalar(
        select(WorkerHeartbeat.last_seen_at).where(WorkerHeartbeat.worker_id == worker_id)
    )
    return last_seen_at is not None and last_seen_at >= now - timedelta(seconds=max_age_seconds)

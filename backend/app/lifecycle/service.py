import stat
from datetime import timedelta
from pathlib import Path
from typing import Protocol
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import add_audit_event
from app.core.exceptions import AppError
from app.db.models import (
    Document,
    DocumentChunk,
    DocumentJob,
    KnowledgeBase,
    SupportAccessGrant,
)
from app.jobs.contracts import HANDLER_FINALIZED, JobLease, ProcessResult
from app.jobs.repository import LeaseLostError, enqueue_job

ACTIVE_JOB_STATUSES = ("pending", "processing", "retry_wait")


class SessionFactory(Protocol):
    def __call__(self) -> AsyncSession: ...


def resolve_upload_file(upload_directory: Path, stored_file_name: str) -> Path:
    upload_root = upload_directory.resolve()
    supplied = Path(stored_file_name)
    if (
        not stored_file_name
        or supplied.is_absolute()
        or supplied.name != stored_file_name
        or "/" in stored_file_name
        or "\\" in stored_file_name
    ):
        raise AppError(
            code="PURGE_PATH_INVALID",
            message="文档存储路径校验失败。",
            status_code=422,
        )
    file_path = upload_root / stored_file_name
    try:
        file_status = file_path.lstat()
    except FileNotFoundError:
        return file_path
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    file_attributes = getattr(file_status, "st_file_attributes", 0)
    if (
        stat.S_ISLNK(file_status.st_mode)
        or (reparse_flag and file_attributes & reparse_flag)
        or not stat.S_ISREG(file_status.st_mode)
    ):
        raise AppError(
            code="PURGE_PATH_INVALID",
            message="文档存储路径校验失败。",
            status_code=422,
        )
    return file_path


async def _database_now(session: AsyncSession):
    now = await session.scalar(select(func.clock_timestamp()))
    assert now is not None
    return now


def _not_found(resource_type: str) -> AppError:
    if resource_type == "document":
        return AppError(code="DOCUMENT_NOT_FOUND", message="文档不存在。", status_code=404)
    return AppError(code="KNOWLEDGE_BASE_NOT_FOUND", message="知识库不存在。", status_code=404)


async def _owned_document(
    session: AsyncSession, owner_user_id: UUID, document_id: UUID, *, for_update: bool
) -> Document:
    statement = (
        select(Document)
        .join(KnowledgeBase, KnowledgeBase.id == Document.knowledge_base_id)
        .where(Document.id == document_id, KnowledgeBase.owner_id == owner_user_id)
    )
    if for_update:
        statement = statement.with_for_update(of=Document)
    document = await session.scalar(statement)
    if document is None:
        raise _not_found("document")
    return document


async def _owned_knowledge_base(
    session: AsyncSession,
    owner_user_id: UUID,
    knowledge_base_id: UUID,
    *,
    for_update: bool,
) -> KnowledgeBase:
    statement = select(KnowledgeBase).where(
        KnowledgeBase.id == knowledge_base_id,
        KnowledgeBase.owner_id == owner_user_id,
    )
    if for_update:
        statement = statement.with_for_update()
    knowledge_base = await session.scalar(statement)
    if knowledge_base is None:
        raise _not_found("knowledge_base")
    return knowledge_base


async def soft_delete_document(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    document_id: UUID,
    retention_days: int,
) -> Document:
    await _owned_document(session, owner_user_id, document_id, for_update=False)
    await session.scalars(
        select(DocumentJob)
        .where(
            DocumentJob.resource_type == "document",
            DocumentJob.resource_id == document_id,
            DocumentJob.status.in_(ACTIVE_JOB_STATUSES),
        )
        .order_by(DocumentJob.id)
        .with_for_update()
    )
    document = await _owned_document(session, owner_user_id, document_id, for_update=True)
    if document.deleted_at is not None:
        now = await _database_now(session)
        await _cancel_jobs(session, DocumentJob.resource_type == "document", document_id, now)
        return document
    now = await _database_now(session)
    document.deleted_at = now
    document.purge_after = now + timedelta(days=retention_days)
    await _cancel_jobs(session, DocumentJob.resource_type == "document", document_id, now)
    add_audit_event(
        session,
        actor_user_id=owner_user_id,
        action="document.delete",
        resource_type="document",
        resource_id=document.id,
        result="success",
        security_summary={"reason": "user_request"},
    )
    return document


async def soft_delete_knowledge_base(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    knowledge_base_id: UUID,
    retention_days: int,
) -> KnowledgeBase:
    await _owned_knowledge_base(session, owner_user_id, knowledge_base_id, for_update=False)
    await session.scalars(
        select(DocumentJob)
        .where(
            DocumentJob.knowledge_base_id == knowledge_base_id,
            DocumentJob.status.in_(ACTIVE_JOB_STATUSES),
        )
        .order_by(DocumentJob.id)
        .with_for_update()
    )
    knowledge_base = await _owned_knowledge_base(
        session, owner_user_id, knowledge_base_id, for_update=True
    )
    if knowledge_base.deleted_at is not None:
        now = await _database_now(session)
        await _cancel_jobs(session, DocumentJob.knowledge_base_id == knowledge_base_id, None, now)
        return knowledge_base
    now = await _database_now(session)
    purge_after = now + timedelta(days=retention_days)
    knowledge_base.deleted_at = now
    knowledge_base.purge_after = purge_after
    await session.execute(
        update(Document)
        .where(
            Document.knowledge_base_id == knowledge_base_id,
            Document.deleted_at.is_(None),
        )
        .values(deleted_at=now, purge_after=purge_after)
    )
    await _cancel_jobs(session, DocumentJob.knowledge_base_id == knowledge_base_id, None, now)
    add_audit_event(
        session,
        actor_user_id=owner_user_id,
        action="knowledge_base.delete",
        resource_type="knowledge_base",
        resource_id=knowledge_base.id,
        result="success",
        security_summary={"reason": "user_request"},
    )
    return knowledge_base


async def _cancel_jobs(session, scope, resource_id: UUID | None, now) -> None:
    conditions = [scope, DocumentJob.status.in_(ACTIVE_JOB_STATUSES)]
    if resource_id is not None:
        conditions.append(DocumentJob.resource_id == resource_id)
    await session.execute(
        update(DocumentJob)
        .where(*conditions)
        .values(
            status="canceled",
            finished_at=now,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
        )
    )


async def restore_document(
    session: AsyncSession, *, owner_user_id: UUID, document_id: UUID
) -> Document:
    existing = await _owned_document(session, owner_user_id, document_id, for_update=False)
    knowledge_base = await _owned_knowledge_base(
        session, owner_user_id, existing.knowledge_base_id, for_update=True
    )
    document = await _owned_document(session, owner_user_id, document_id, for_update=True)
    if document.deleted_at is None:
        return document
    if knowledge_base.deleted_at is not None:
        raise AppError(
            code="KNOWLEDGE_BASE_IN_TRASH",
            message="请先恢复文档所属知识库。",
            status_code=409,
        )
    now = await _database_now(session)
    if document.purge_after is None or document.purge_after <= now:
        raise AppError(
            code="DOCUMENT_RETENTION_EXPIRED",
            message="文档恢复期已结束，只能永久清理。",
            status_code=409,
        )
    duplicate = await session.scalar(
        select(Document.id).where(
            Document.knowledge_base_id == document.knowledge_base_id,
            Document.file_hash == document.file_hash,
            Document.deleted_at.is_(None),
            Document.id != document.id,
        )
    )
    if duplicate is not None:
        raise AppError(
            code="DOCUMENT_RESTORE_CONFLICT",
            message="知识库中已存在相同文件，无法恢复。",
            status_code=409,
        )
    document.deleted_at = None
    document.purge_after = None
    add_audit_event(
        session,
        actor_user_id=owner_user_id,
        action="document.restore",
        resource_type="document",
        resource_id=document.id,
        result="success",
        security_summary={"reason": "user_request"},
    )
    return document


async def restore_knowledge_base(
    session: AsyncSession, *, owner_user_id: UUID, knowledge_base_id: UUID
) -> KnowledgeBase:
    knowledge_base = await _owned_knowledge_base(
        session, owner_user_id, knowledge_base_id, for_update=True
    )
    if knowledge_base.deleted_at is None:
        return knowledge_base
    now = await _database_now(session)
    if knowledge_base.purge_after is None or knowledge_base.purge_after <= now:
        raise AppError(
            code="KNOWLEDGE_BASE_RETENTION_EXPIRED",
            message="知识库恢复期已结束，只能永久清理。",
            status_code=409,
        )
    cascade_deleted_at = knowledge_base.deleted_at
    restoring_ids = select(Document.id).where(
        Document.knowledge_base_id == knowledge_base_id,
        Document.deleted_at == cascade_deleted_at,
    )
    conflict = await session.scalar(
        select(Document.id)
        .where(
            Document.knowledge_base_id == knowledge_base_id,
            Document.deleted_at.is_(None),
            Document.file_hash.in_(
                select(Document.file_hash).where(Document.id.in_(restoring_ids))
            ),
        )
        .limit(1)
    )
    if conflict is not None:
        raise AppError(
            code="DOCUMENT_RESTORE_CONFLICT",
            message="知识库中已存在相同文件，无法恢复。",
            status_code=409,
        )
    await session.execute(
        update(Document)
        .where(Document.id.in_(restoring_ids))
        .values(deleted_at=None, purge_after=None)
    )
    knowledge_base.deleted_at = None
    knowledge_base.purge_after = None
    add_audit_event(
        session,
        actor_user_id=owner_user_id,
        action="knowledge_base.restore",
        resource_type="knowledge_base",
        resource_id=knowledge_base.id,
        result="success",
        security_summary={"reason": "user_request"},
    )
    return knowledge_base


async def request_purge_document(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    document_id: UUID,
    max_attempts: int,
    retention_days: int = 7,
) -> DocumentJob:
    existing = await _owned_document(session, owner_user_id, document_id, for_update=False)
    await _owned_knowledge_base(session, owner_user_id, existing.knowledge_base_id, for_update=True)
    document = await _owned_document(session, owner_user_id, document_id, for_update=True)
    return await _request_purge(
        session,
        owner_user_id=owner_user_id,
        resource_type="document",
        resource_id=document.id,
        knowledge_base_id=document.knowledge_base_id,
        deleted_at=document.deleted_at,
        purge_after=document.purge_after,
        max_attempts=max_attempts,
        retention_days=retention_days,
    )


async def request_purge_knowledge_base(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    knowledge_base_id: UUID,
    max_attempts: int,
    retention_days: int = 7,
) -> DocumentJob:
    knowledge_base = await _owned_knowledge_base(
        session, owner_user_id, knowledge_base_id, for_update=True
    )
    return await _request_purge(
        session,
        owner_user_id=owner_user_id,
        resource_type="knowledge_base",
        resource_id=knowledge_base.id,
        knowledge_base_id=knowledge_base.id,
        deleted_at=knowledge_base.deleted_at,
        purge_after=knowledge_base.purge_after,
        max_attempts=max_attempts,
        retention_days=retention_days,
    )


async def _request_purge(
    session,
    *,
    owner_user_id,
    resource_type,
    resource_id,
    knowledge_base_id,
    deleted_at,
    purge_after,
    max_attempts,
    retention_days,
):
    if deleted_at is None or purge_after is None:
        raise AppError(
            code="RESOURCE_NOT_IN_TRASH",
            message="资源不在回收站中。",
            status_code=409,
        )
    now = await _database_now(session)
    if max(purge_after, deleted_at + timedelta(days=retention_days)) > now:
        raise AppError(
            code="PURGE_RETENTION_ACTIVE",
            message="恢复期内不能永久清理。",
            status_code=409,
        )
    job_type = f"purge_{resource_type}"
    active_job = await session.scalar(
        select(DocumentJob).where(
            DocumentJob.job_type == job_type,
            DocumentJob.resource_type == resource_type,
            DocumentJob.resource_id == resource_id,
            DocumentJob.status.in_(ACTIVE_JOB_STATUSES),
        )
    )
    if active_job is not None:
        return active_job
    job = await enqueue_job(
        session,
        job_type=job_type,
        resource_type=resource_type,
        resource_id=resource_id,
        owner_user_id=owner_user_id,
        knowledge_base_id=knowledge_base_id,
        max_attempts=max_attempts,
    )
    add_audit_event(
        session,
        actor_user_id=owner_user_id,
        action=f"{resource_type}.purge_request",
        resource_type=resource_type,
        resource_id=resource_id,
        result="success",
        security_summary={"reason": "retention_expired"},
    )
    return job


async def purge_document(
    *,
    session_factory: SessionFactory,
    upload_directory: Path,
    lease: JobLease,
    retention_days: int = 7,
) -> ProcessResult:
    if lease.job_type != "purge_document" or lease.resource_type != "document":
        raise ValueError("purge_document 仅处理文档清理任务")
    async with session_factory() as session:
        try:
            return await _purge_document_in_session(
                session, upload_directory, lease, retention_days
            )
        except AppError as error:
            await _record_purge_denial(session, lease, error)
            raise


async def _purge_document_in_session(
    session: AsyncSession,
    upload_directory: Path,
    lease: JobLease,
    retention_days: int,
) -> ProcessResult:
    job = await session.scalar(
        select(DocumentJob).where(DocumentJob.id == lease.job_id).with_for_update()
    )
    document = await session.get(Document, lease.resource_id, with_for_update=True)
    if job is None and document is None:
        await session.rollback()
        return ProcessResult(chunk_count=0, completion_mode=HANDLER_FINALIZED)
    await _validate_purge_lease(session, job, lease)
    if document is None:
        raise LeaseLostError("待清理文档已不存在")
    now = await _database_now(session)
    _validate_purge_time(document.deleted_at, document.purge_after, now, retention_days)
    stored_file = resolve_upload_file(upload_directory, document.stored_file_name)
    _unlink_file(stored_file)
    await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
    add_audit_event(
        session,
        actor_user_id=lease.owner_user_id,
        action="document.purge",
        resource_type="document",
        resource_id=document.id,
        result="success",
        security_summary={"reason": "retention_expired"},
        request_id=f"worker:{lease.job_id}",
    )
    await session.execute(
        delete(DocumentJob).where(
            DocumentJob.resource_type == "document",
            DocumentJob.resource_id == document.id,
        )
    )
    await session.delete(document)
    await session.commit()
    return ProcessResult(chunk_count=0, completion_mode=HANDLER_FINALIZED)


async def purge_knowledge_base(
    *,
    session_factory: SessionFactory,
    upload_directory: Path,
    lease: JobLease,
    retention_days: int = 7,
) -> ProcessResult:
    if lease.job_type != "purge_knowledge_base" or lease.resource_type != "knowledge_base":
        raise ValueError("purge_knowledge_base 仅处理知识库清理任务")
    async with session_factory() as session:
        try:
            return await _purge_knowledge_base_in_session(
                session, upload_directory, lease, retention_days
            )
        except AppError as error:
            await _record_purge_denial(session, lease, error)
            raise


async def _purge_knowledge_base_in_session(
    session: AsyncSession,
    upload_directory: Path,
    lease: JobLease,
    retention_days: int,
) -> ProcessResult:
    job = await session.scalar(
        select(DocumentJob).where(DocumentJob.id == lease.job_id).with_for_update()
    )
    knowledge_base = await session.get(KnowledgeBase, lease.resource_id, with_for_update=True)
    if job is None and knowledge_base is None:
        await session.rollback()
        return ProcessResult(chunk_count=0, completion_mode=HANDLER_FINALIZED)
    await _validate_purge_lease(session, job, lease)
    if knowledge_base is None:
        raise LeaseLostError("待清理知识库已不存在")
    now = await _database_now(session)
    _validate_purge_time(
        knowledge_base.deleted_at,
        knowledge_base.purge_after,
        now,
        retention_days,
    )
    documents = (
        await session.scalars(
            select(Document)
            .where(Document.knowledge_base_id == knowledge_base.id)
            .order_by(Document.id)
            .with_for_update()
        )
    ).all()
    stored_files = [
        resolve_upload_file(upload_directory, document.stored_file_name) for document in documents
    ]
    for stored_file in stored_files:
        _unlink_file(stored_file)
    await session.execute(
        delete(DocumentChunk).where(DocumentChunk.knowledge_base_id == knowledge_base.id)
    )
    await session.execute(
        delete(SupportAccessGrant).where(SupportAccessGrant.knowledge_base_id == knowledge_base.id)
    )
    await session.execute(delete(Document).where(Document.knowledge_base_id == knowledge_base.id))
    add_audit_event(
        session,
        actor_user_id=lease.owner_user_id,
        action="knowledge_base.purge",
        resource_type="knowledge_base",
        resource_id=knowledge_base.id,
        result="success",
        security_summary={"reason": "retention_expired"},
        request_id=f"worker:{lease.job_id}",
    )
    await session.execute(
        delete(DocumentJob).where(DocumentJob.knowledge_base_id == knowledge_base.id)
    )
    await session.delete(knowledge_base)
    await session.commit()
    return ProcessResult(chunk_count=0, completion_mode=HANDLER_FINALIZED)


async def _record_purge_denial(session: AsyncSession, lease: JobLease, error: AppError) -> None:
    add_audit_event(
        session,
        actor_user_id=lease.owner_user_id,
        action=f"{lease.resource_type}.purge",
        resource_type=lease.resource_type,
        resource_id=lease.resource_id,
        result="denied",
        security_summary={"reason": error.code},
        request_id=f"worker:{lease.job_id}",
    )
    await session.commit()


async def _validate_purge_lease(session, job, lease) -> None:
    now = await _database_now(session)
    if (
        job is None
        or job.status != "processing"
        or job.lease_token != lease.lease_token
        or job.lease_expires_at is None
        or job.lease_expires_at < now
        or job.job_type != lease.job_type
        or job.resource_type != lease.resource_type
        or job.resource_id != lease.resource_id
    ):
        raise LeaseLostError("永久清理任务租约已失效")


def _validate_purge_time(deleted_at, purge_after, now, retention_days: int) -> None:
    if deleted_at is None or purge_after is None:
        raise AppError(
            code="RESOURCE_NOT_IN_TRASH",
            message="资源不在回收站中。",
            status_code=409,
        )
    if max(purge_after, deleted_at + timedelta(days=retention_days)) > now:
        raise AppError(
            code="PURGE_RETENTION_ACTIVE",
            message="恢复期内不能永久清理。",
            status_code=409,
        )


def _unlink_file(file_path: Path) -> None:
    try:
        file_path.unlink(missing_ok=True)
    except OSError as error:
        raise AppError(
            code="PURGE_FILE_DELETE_FAILED",
            message="文档文件暂时无法清理。",
            status_code=500,
        ) from error

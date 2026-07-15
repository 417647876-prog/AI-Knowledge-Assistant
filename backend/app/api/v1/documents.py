import hashlib
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import get_current_user
from app.authorization.service import (
    get_accessible_document,
    get_accessible_knowledge_base,
)
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.db.models import Document, DocumentJob, User
from app.db.session import get_session
from app.knowledge.background import run_ingestion

router = APIRouter(tags=["documents"])
_allowed_extensions = {".pdf", ".docx", ".xlsx", ".md", ".txt"}


class DocumentTaskResponse(BaseModel):
    document_id: UUID
    job_id: UUID
    file_name: str
    status: Literal["pending", "parsing", "embedding", "ready", "failed"]
    error_code: str | None
    error_message: str | None


class DocumentListResponse(BaseModel):
    items: list[DocumentTaskResponse]


def _document_response(document: Document, job: DocumentJob) -> DocumentTaskResponse:
    return DocumentTaskResponse(
        document_id=document.id,
        job_id=job.id,
        file_name=document.original_file_name,
        status=document.status,
        error_code=document.error_code,
        error_message=document.error_message,
    )


async def _latest_job(session: AsyncSession, document_id: UUID) -> DocumentJob | None:
    return await session.scalar(
        select(DocumentJob)
        .where(
            DocumentJob.job_type == "ingest_document",
            DocumentJob.resource_type == "document",
            DocumentJob.resource_id == document_id,
        )
        .order_by(DocumentJob.created_at.desc(), DocumentJob.id.desc())
        .limit(1)
    )


@router.post(
    "/api/v1/knowledge-bases/{knowledge_base_id}/documents",
    response_model=DocumentTaskResponse,
    status_code=202,
)
async def upload_document(
    knowledge_base_id: UUID,
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File()],
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DocumentTaskResponse:
    knowledge_base = await get_accessible_knowledge_base(session, current_user, knowledge_base_id)
    extension = Path(file.filename or "").suffix.lower()
    if extension not in _allowed_extensions:
        raise AppError(
            code="UNSUPPORTED_FILE_TYPE", message="当前不支持该文件格式。", status_code=415
        )
    settings = get_settings()
    content = bytearray()
    while chunk := await file.read(64 * 1024):
        content.extend(chunk)
        if len(content) > settings.max_upload_bytes:
            raise AppError(code="FILE_TOO_LARGE", message="文件超过 20 MB 限制。", status_code=413)
    if not content:
        raise AppError(code="DOCUMENT_CONTENT_EMPTY", message="文档内容为空。", status_code=422)
    file_hash = hashlib.sha256(content).hexdigest()
    duplicate = await session.scalar(
        select(Document.id).where(
            Document.knowledge_base_id == knowledge_base_id, Document.file_hash == file_hash
        )
    )
    if duplicate is not None:
        raise AppError(
            code="DUPLICATE_DOCUMENT", message="该知识库已上传相同文件。", status_code=409
        )
    stored_file_name = f"{uuid4()}{extension}"
    settings.upload_directory.mkdir(parents=True, exist_ok=True)
    (settings.upload_directory / stored_file_name).write_bytes(content)
    document = Document(
        knowledge_base_id=knowledge_base_id,
        original_file_name=file.filename or "upload",
        stored_file_name=stored_file_name,
        content_type=file.content_type or "application/octet-stream",
        file_extension=extension,
        file_size=len(content),
        file_hash=file_hash,
    )
    session.add(document)
    await session.flush()
    job = DocumentJob(
        job_type="ingest_document",
        resource_type="document",
        resource_id=document.id,
        owner_user_id=knowledge_base.owner_id,
        knowledge_base_id=knowledge_base.id,
        stage="parse",
    )
    session.add(job)
    await session.commit()
    background_tasks.add_task(run_ingestion, document.id)
    return _document_response(document, job)


@router.get("/api/v1/documents/{document_id}", response_model=DocumentTaskResponse)
async def get_document(
    document_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DocumentTaskResponse:
    document = await get_accessible_document(session, current_user, document_id)
    job = await _latest_job(session, document_id)
    if job is None:
        raise AppError(code="DOCUMENT_NOT_FOUND", message="文档任务不存在。", status_code=404)
    return _document_response(document, job)


@router.get(
    "/api/v1/knowledge-bases/{knowledge_base_id}/documents",
    response_model=DocumentListResponse,
)
async def list_documents(
    knowledge_base_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DocumentListResponse:
    await get_accessible_knowledge_base(session, current_user, knowledge_base_id)
    documents = (
        await session.scalars(
            select(Document)
            .where(Document.knowledge_base_id == knowledge_base_id)
            .order_by(Document.created_at.desc(), Document.id.desc())
        )
    ).all()
    items: list[DocumentTaskResponse] = []
    for document in documents:
        job = await _latest_job(session, document.id)
        if job is not None:
            items.append(_document_response(document, job))
    return DocumentListResponse(items=items)


@router.post(
    "/api/v1/documents/{document_id}/reprocess",
    response_model=DocumentTaskResponse,
    status_code=202,
)
async def reprocess_document(
    document_id: UUID,
    background_tasks: BackgroundTasks,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DocumentTaskResponse:
    document = await get_accessible_document(session, current_user, document_id, for_update=True)
    knowledge_base = await get_accessible_knowledge_base(
        session, current_user, document.knowledge_base_id
    )
    active_job = await session.scalar(
        select(DocumentJob.id).where(
            DocumentJob.job_type == "ingest_document",
            DocumentJob.resource_type == "document",
            DocumentJob.resource_id == document_id,
            DocumentJob.status.in_(("pending", "processing", "retry_wait")),
        )
    )
    if active_job is not None:
        raise AppError(
            code="DOCUMENT_PROCESSING",
            message="文档正在处理中，请勿重复提交。",
            status_code=409,
        )
    document.status = "pending"
    document.error_code = None
    document.error_message = None
    job = DocumentJob(
        job_type="ingest_document",
        resource_type="document",
        resource_id=document.id,
        owner_user_id=knowledge_base.owner_id,
        knowledge_base_id=knowledge_base.id,
        stage="parse",
    )
    session.add(job)
    await session.commit()
    background_tasks.add_task(run_ingestion, document.id)
    return _document_response(document, job)


@router.delete("/api/v1/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    document = await get_accessible_document(session, current_user, document_id, for_update=True)
    active_job = await session.scalar(
        select(DocumentJob.id).where(
            DocumentJob.job_type == "ingest_document",
            DocumentJob.resource_type == "document",
            DocumentJob.resource_id == document_id,
            DocumentJob.status.in_(("pending", "processing", "retry_wait")),
        )
    )
    if active_job is not None:
        raise AppError(
            code="DOCUMENT_PROCESSING",
            message="文档正在处理中，请稍后再删除。",
            status_code=409,
        )

    upload_root = get_settings().upload_directory.resolve()
    stored_file = (upload_root / document.stored_file_name).resolve()
    if not stored_file.is_relative_to(upload_root):
        raise AppError(
            code="DOCUMENT_DELETE_FAILED",
            message="文档删除失败，请稍后重试。",
            status_code=500,
        )
    try:
        await session.execute(
            delete(DocumentJob).where(
                DocumentJob.job_type == "ingest_document",
                DocumentJob.resource_type == "document",
                DocumentJob.resource_id == document_id,
            )
        )
        await session.delete(document)
        await session.flush()
        stored_file.unlink(missing_ok=True)
        await session.commit()
    except OSError as error:
        await session.rollback()
        raise AppError(
            code="DOCUMENT_DELETE_FAILED",
            message="文档删除失败，请稍后重试。",
            status_code=500,
        ) from error
    return Response(status_code=204)

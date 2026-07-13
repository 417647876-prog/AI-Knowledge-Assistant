import hashlib
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.db.models.document import Document
from app.db.models.ingestion_job import IngestionJob
from app.db.models.knowledge_base import KnowledgeBase
from app.db.session import get_session
from app.knowledge.background import run_ingestion

router = APIRouter(tags=["documents"])
_allowed_extensions = {".pdf", ".docx", ".xlsx", ".md", ".txt"}


def _document_response(document: Document, job: IngestionJob) -> dict[str, str | None]:
    return {
        "document_id": str(document.id),
        "job_id": str(job.id),
        "status": document.status,
        "error_code": document.error_code,
        "error_message": document.error_message,
    }


@router.post("/api/v1/knowledge-bases/{knowledge_base_id}/documents", status_code=202)
async def upload_document(
    knowledge_base_id: UUID,
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File()],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str | None]:
    extension = Path(file.filename or "").suffix.lower()
    if extension not in _allowed_extensions:
        raise AppError(
            code="UNSUPPORTED_FILE_TYPE", message="当前不支持该文件格式。", status_code=415
        )
    content = await file.read()
    settings = get_settings()
    if not content:
        raise AppError(code="DOCUMENT_CONTENT_EMPTY", message="文档内容为空。", status_code=422)
    if len(content) > settings.max_upload_bytes:
        raise AppError(code="FILE_TOO_LARGE", message="文件超过 20 MB 限制。", status_code=413)
    if await session.get(KnowledgeBase, knowledge_base_id) is None:
        raise AppError(code="KNOWLEDGE_BASE_NOT_FOUND", message="知识库不存在。", status_code=404)
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
    job = IngestionJob(document_id=document.id)
    session.add(job)
    await session.commit()
    background_tasks.add_task(run_ingestion, document.id)
    return _document_response(document, job)


@router.get("/api/v1/documents/{document_id}")
async def get_document(
    document_id: UUID, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, str | None]:
    document = await session.get(Document, document_id)
    if document is None:
        raise AppError(code="DOCUMENT_NOT_FOUND", message="文档不存在。", status_code=404)
    job = await session.scalar(
        select(IngestionJob)
        .where(IngestionJob.document_id == document_id)
        .order_by(IngestionJob.id.desc())
    )
    if job is None:
        raise AppError(code="DOCUMENT_NOT_FOUND", message="文档任务不存在。", status_code=404)
    return _document_response(document, job)

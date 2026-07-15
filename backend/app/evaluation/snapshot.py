import hashlib
import json
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.document import Document
from app.db.models.document_chunk import DocumentChunk
from app.db.models.knowledge_base import KnowledgeBase


@dataclass(frozen=True)
class KnowledgeBaseSnapshot:
    knowledge_base_id: UUID
    snapshot_sha256: str
    document_count: int
    chunk_count: int


def _canonical_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _write_field(hasher: Any, name: str, value: Any) -> None:
    name_bytes = name.encode("utf-8")
    value_bytes = _canonical_bytes(value)
    hasher.update(struct.pack("!I", len(name_bytes)))
    hasher.update(name_bytes)
    hasher.update(struct.pack("!Q", len(value_bytes)))
    hasher.update(value_bytes)


def _embedding_bytes(values: Sequence[float]) -> bytes:
    return b"".join(struct.pack("!d", float(value)) for value in values)


def build_knowledge_base_snapshot(
    knowledge_base_id: UUID,
    documents: Sequence[Document],
    chunks: Sequence[DocumentChunk],
) -> KnowledgeBaseSnapshot:
    ordered_documents = sorted(documents, key=lambda item: str(item.id))
    ordered_chunks = sorted(
        chunks,
        key=lambda item: (str(item.document_id), item.chunk_index, str(item.id)),
    )
    hasher = hashlib.sha256()
    _write_field(hasher, "knowledge_base_id", str(knowledge_base_id))
    _write_field(hasher, "document_count", len(ordered_documents))
    _write_field(hasher, "chunk_count", len(ordered_chunks))

    for document in ordered_documents:
        _write_field(hasher, "document.id", str(document.id))
        _write_field(hasher, "document.original_file_name", document.original_file_name)
        _write_field(hasher, "document.file_hash", document.file_hash)
        _write_field(hasher, "document.status", document.status)

    for chunk in ordered_chunks:
        _write_field(hasher, "chunk.id", str(chunk.id))
        _write_field(hasher, "chunk.document_id", str(chunk.document_id))
        _write_field(hasher, "chunk.chunk_index", chunk.chunk_index)
        _write_field(hasher, "chunk.content_hash", chunk.content_hash)
        _write_field(hasher, "chunk.content", chunk.content)
        _write_field(hasher, "chunk.search_text", chunk.search_text)
        _write_field(hasher, "chunk.page_number", chunk.page_number)
        _write_field(hasher, "chunk.sheet_name", chunk.sheet_name)
        _write_field(hasher, "chunk.row_start", chunk.row_start)
        _write_field(hasher, "chunk.section_title", chunk.section_title)
        _write_field(hasher, "chunk.start_index", chunk.start_index)
        _write_field(hasher, "chunk.extra_metadata", chunk.extra_metadata)
        _write_field(hasher, "chunk.embedding", _embedding_bytes(chunk.embedding))

    return KnowledgeBaseSnapshot(
        knowledge_base_id=knowledge_base_id,
        snapshot_sha256=hasher.hexdigest(),
        document_count=len(ordered_documents),
        chunk_count=len(ordered_chunks),
    )


async def compute_knowledge_base_snapshot(
    session: AsyncSession,
    knowledge_base_id: UUID,
) -> KnowledgeBaseSnapshot:
    existing_id = await session.scalar(
        select(KnowledgeBase.id).where(KnowledgeBase.id == knowledge_base_id)
    )
    if existing_id is None:
        raise ValueError("目标知识库不存在")

    documents = list(
        await session.scalars(
            select(Document)
            .where(Document.knowledge_base_id == knowledge_base_id)
            .order_by(Document.id)
        )
    )
    chunks = list(
        await session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.knowledge_base_id == knowledge_base_id)
            .order_by(
                DocumentChunk.document_id,
                DocumentChunk.chunk_index,
                DocumentChunk.id,
            )
        )
    )
    return build_knowledge_base_snapshot(knowledge_base_id, documents, chunks)

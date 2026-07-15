from copy import deepcopy
from datetime import UTC, datetime
from importlib import import_module
from uuid import UUID

import pytest

from app.db.models.document import Document
from app.db.models.document_chunk import DocumentChunk

KNOWLEDGE_BASE_ID = UUID("11111111-1111-1111-1111-111111111111")
FIRST_DOCUMENT_ID = UUID("22222222-2222-2222-2222-222222222222")
SECOND_DOCUMENT_ID = UUID("33333333-3333-3333-3333-333333333333")
FIRST_CHUNK_ID = UUID("44444444-4444-4444-4444-444444444444")
SECOND_CHUNK_ID = UUID("55555555-5555-5555-5555-555555555555")


def make_documents() -> list[Document]:
    return [
        Document(
            id=FIRST_DOCUMENT_ID,
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            original_file_name="员工手册.docx",
            stored_file_name="stored-first.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            file_extension=".docx",
            file_size=1024,
            file_hash="a" * 64,
            status="completed",
        ),
        Document(
            id=SECOND_DOCUMENT_ID,
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            original_file_name="报销制度.md",
            stored_file_name="stored-second.md",
            content_type="text/markdown",
            file_extension=".md",
            file_size=512,
            file_hash="b" * 64,
            status="completed",
        ),
    ]


def make_chunks() -> list[DocumentChunk]:
    return [
        DocumentChunk(
            id=FIRST_CHUNK_ID,
            document_id=FIRST_DOCUMENT_ID,
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            chunk_index=0,
            content="员工试用期为三个月。",
            content_hash="c" * 64,
            page_number=1,
            sheet_name=None,
            row_start=None,
            section_title="入职",
            start_index=0,
            extra_metadata={"source": "word", "level": 1},
            embedding=[0.1] * 512,
            search_text="员工 员工试 试用 用期",
        ),
        DocumentChunk(
            id=SECOND_CHUNK_ID,
            document_id=SECOND_DOCUMENT_ID,
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            chunk_index=0,
            content="单笔报销应在三十天内提交。",
            content_hash="d" * 64,
            page_number=None,
            sheet_name="制度",
            row_start=2,
            section_title="报销时限",
            start_index=10,
            extra_metadata={"source": "markdown"},
            embedding=[0.2] * 512,
            search_text="单笔 报销 三十天",
        ),
    ]


def build_snapshot(
    documents: list[Document],
    chunks: list[DocumentChunk],
    *,
    knowledge_base_id: UUID = KNOWLEDGE_BASE_ID,
):
    module = import_module("app.evaluation.snapshot")
    return module.build_knowledge_base_snapshot(knowledge_base_id, documents, chunks)


def test_snapshot_is_stable_across_input_order() -> None:
    documents = make_documents()
    chunks = make_chunks()

    first = build_snapshot(documents, chunks)
    second = build_snapshot(list(reversed(documents)), list(reversed(chunks)))

    assert first == second
    assert first.document_count == 2
    assert first.chunk_count == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("content", "变化后的正文"),
        ("content_hash", "e" * 64),
        ("search_text", "变化 token"),
        ("page_number", 9),
        ("sheet_name", "变化工作表"),
        ("row_start", 99),
        ("section_title", "变化章节"),
        ("start_index", 88),
        ("extra_metadata", {"source": "changed"}),
        ("embedding", [0.3] * 512),
    ],
)
def test_chunk_retrieval_field_change_changes_snapshot(field: str, value: object) -> None:
    documents = make_documents()
    chunks = make_chunks()
    original = build_snapshot(documents, chunks)

    setattr(chunks[0], field, value)
    changed = build_snapshot(documents, chunks)

    assert changed.snapshot_sha256 != original.snapshot_sha256


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("original_file_name", "变化后的文件名.docx"),
        ("file_hash", "f" * 64),
        ("status", "failed"),
    ],
)
def test_document_retrieval_field_change_changes_snapshot(field: str, value: str) -> None:
    documents = make_documents()
    chunks = make_chunks()
    original = build_snapshot(documents, chunks)

    setattr(documents[0], field, value)
    changed = build_snapshot(documents, chunks)

    assert changed.snapshot_sha256 != original.snapshot_sha256


def test_timestamp_and_unrelated_document_fields_are_not_snapshot_inputs() -> None:
    documents = make_documents()
    chunks = make_chunks()
    original = build_snapshot(documents, chunks)

    documents[0].created_at = datetime.now(UTC)
    documents[0].updated_at = datetime.now(UTC)
    documents[0].stored_file_name = "changed-storage-name.docx"
    documents[0].error_message = "changed error"

    assert build_snapshot(documents, chunks) == original


def test_length_prefix_prevents_adjacent_field_collision() -> None:
    first_documents = make_documents()
    second_documents = deepcopy(first_documents)
    chunks = make_chunks()
    first_documents[0].original_file_name = "a|b"
    first_documents[0].file_hash = "c"
    second_documents[0].original_file_name = "a"
    second_documents[0].file_hash = "b|c"

    first = build_snapshot(first_documents, chunks)
    second = build_snapshot(second_documents, chunks)

    assert first.snapshot_sha256 != second.snapshot_sha256


def test_knowledge_base_id_is_part_of_snapshot() -> None:
    documents = make_documents()
    chunks = make_chunks()
    first = build_snapshot(documents, chunks)
    second = build_snapshot(
        documents,
        chunks,
        knowledge_base_id=UUID("66666666-6666-6666-6666-666666666666"),
    )

    assert first.snapshot_sha256 != second.snapshot_sha256

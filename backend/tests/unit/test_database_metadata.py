from sqlalchemy import UniqueConstraint

from app.db.base import Base
from app.db.models import Document, DocumentChunk, IngestionJob, KnowledgeBase


def test_stage_1a_metadata_contains_four_core_tables() -> None:
    assert set(Base.metadata.tables) == {
        "knowledge_bases",
        "documents",
        "document_chunks",
        "ingestion_jobs",
    }
    assert KnowledgeBase.__tablename__ == "knowledge_bases"
    assert Document.__tablename__ == "documents"
    assert DocumentChunk.__tablename__ == "document_chunks"
    assert IngestionJob.__tablename__ == "ingestion_jobs"


def test_document_duplicate_constraint_is_scoped_to_knowledge_base() -> None:
    constraints = [
        constraint
        for constraint in Document.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    column_sets = [{column.name for column in item.columns} for item in constraints]

    assert {"knowledge_base_id", "file_hash"} in column_sets


def test_document_chunk_embedding_is_512_dimensions() -> None:
    embedding_type = DocumentChunk.__table__.c.embedding.type

    assert embedding_type.dim == 512

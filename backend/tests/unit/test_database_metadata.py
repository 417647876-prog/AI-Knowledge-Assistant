from sqlalchemy import CHAR, CheckConstraint, Text, UniqueConstraint

from app.db.base import Base
from app.db.models import (
    ADMIN_ROLE,
    USER_ROLE,
    Document,
    DocumentChunk,
    IngestionJob,
    KnowledgeBase,
    RefreshSession,
    User,
)


def test_metadata_contains_six_core_tables() -> None:
    assert set(Base.metadata.tables) == {
        "knowledge_bases",
        "documents",
        "document_chunks",
        "ingestion_jobs",
        "users",
        "refresh_sessions",
    }
    assert KnowledgeBase.__tablename__ == "knowledge_bases"
    assert Document.__tablename__ == "documents"
    assert DocumentChunk.__tablename__ == "document_chunks"
    assert IngestionJob.__tablename__ == "ingestion_jobs"
    assert User.__tablename__ == "users"
    assert RefreshSession.__tablename__ == "refresh_sessions"


def test_auth_models_enforce_unique_identity_and_ownership() -> None:
    assert KnowledgeBase.__table__.c.owner_id.nullable is False
    assert len(KnowledgeBase.__table__.c.owner_id.foreign_keys) == 1
    assert User.__table__.c.username.unique is True
    assert RefreshSession.__table__.c.token_hash.unique is True
    assert User.__table__.c.username.type.length == 50
    assert isinstance(User.__table__.c.password_hash.type, Text)
    assert isinstance(RefreshSession.__table__.c.token_hash.type, CHAR)
    assert RefreshSession.__table__.c.token_hash.type.length == 64
    assert "replaced_by_id" in RefreshSession.__table__.c
    assert "replaced_by_session_id" not in RefreshSession.__table__.c

    role_checks = [
        constraint
        for constraint in User.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    ]
    assert any(
        "role IN ('admin', 'user')" in str(constraint.sqltext)
        for constraint in role_checks
    )


def test_user_normalizes_username_and_exports_roles() -> None:
    user = User(username="  Admin  ", password_hash="hashed")

    assert user.username == "admin"
    assert (ADMIN_ROLE, USER_ROLE) == ("admin", "user")


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

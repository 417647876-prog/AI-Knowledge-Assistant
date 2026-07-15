from dataclasses import fields
from typing import get_args

from sqlalchemy import CHAR, CheckConstraint, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR

from app.db.base import Base
from app.db.models import (
    ADMIN_ROLE,
    USER_ROLE,
    AuditEvent,
    Document,
    DocumentChunk,
    DocumentJob,
    KnowledgeBase,
    RefreshSession,
    SupportAccessGrant,
    User,
    WorkerHeartbeat,
)
from app.jobs.contracts import JobLease, JobStatus, JobType


def test_metadata_contains_private_lifecycle_tables() -> None:
    assert set(Base.metadata.tables) == {
        "knowledge_bases",
        "documents",
        "document_chunks",
        "document_jobs",
        "worker_heartbeats",
        "users",
        "refresh_sessions",
        "support_access_grants",
        "audit_events",
    }
    assert KnowledgeBase.__tablename__ == "knowledge_bases"
    assert Document.__tablename__ == "documents"
    assert DocumentChunk.__tablename__ == "document_chunks"
    assert DocumentJob.__tablename__ == "document_jobs"
    assert WorkerHeartbeat.__tablename__ == "worker_heartbeats"
    assert User.__tablename__ == "users"
    assert RefreshSession.__tablename__ == "refresh_sessions"
    assert SupportAccessGrant.__tablename__ == "support_access_grants"
    assert AuditEvent.__tablename__ == "audit_events"


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
    assert any("role IN ('admin', 'user')" in str(constraint.sqltext) for constraint in role_checks)


def test_user_normalizes_username_and_exports_roles() -> None:
    user = User(username="  Admin  ", password_hash="hashed")

    assert user.username == "admin"
    assert (ADMIN_ROLE, USER_ROLE) == ("admin", "user")


def test_private_resources_declare_lifecycle_and_uploader_contract() -> None:
    assert {"deleted_at", "purge_after"}.issubset(KnowledgeBase.__table__.c.keys())
    assert {"uploaded_by_user_id", "deleted_at", "purge_after"}.issubset(
        Document.__table__.c.keys()
    )
    assert Document.__table__.c.uploaded_by_user_id.nullable is False
    assert len(Document.__table__.c.uploaded_by_user_id.foreign_keys) == 1

    knowledge_base_foreign_key = next(iter(Document.__table__.c.knowledge_base_id.foreign_keys))
    assert knowledge_base_foreign_key.ondelete != "CASCADE"


def test_document_duplicate_constraint_only_applies_to_active_documents() -> None:
    constraints = [
        constraint
        for constraint in Document.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    column_sets = [{column.name for column in item.columns} for item in constraints]

    assert {"knowledge_base_id", "file_hash"} not in column_sets

    index = next(
        item
        for item in Document.__table__.indexes
        if item.name == "uq_documents_active_knowledge_base_file_hash"
    )
    assert index.unique is True
    assert {column.name for column in index.columns} == {"knowledge_base_id", "file_hash"}
    assert str(index.dialect_options["postgresql"]["where"]) == "deleted_at IS NULL"


def test_support_grant_is_read_only_and_has_safe_validity_constraints() -> None:
    columns = SupportAccessGrant.__table__.c
    assert {
        "knowledge_base_id",
        "owner_user_id",
        "admin_user_id",
        "access_level",
        "expires_at",
        "revoked_at",
        "created_at",
        "last_used_at",
    }.issubset(columns.keys())
    assert columns.access_level.default.arg == "read_only"
    assert columns.access_level.server_default.arg == "read_only"

    checks = [
        str(constraint.sqltext)
        for constraint in SupportAccessGrant.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    ]
    assert "access_level = 'read_only'" in checks
    assert "owner_user_id <> admin_user_id" in checks
    assert "expires_at > created_at" in checks


def test_audit_event_keeps_resource_identity_without_business_foreign_key() -> None:
    columns = AuditEvent.__table__.c
    assert {
        "actor_user_id",
        "action",
        "resource_type",
        "resource_id",
        "result",
        "security_summary",
        "request_id",
        "created_at",
    }.issubset(columns.keys())
    assert len(columns.resource_id.foreign_keys) == 0
    assert isinstance(columns.security_summary.type, JSONB)


def test_document_chunk_embedding_is_512_dimensions() -> None:
    embedding_type = DocumentChunk.__table__.c.embedding.type

    assert embedding_type.dim == 512


def test_document_chunk_contains_generated_search_columns() -> None:
    search_text = DocumentChunk.__table__.c.search_text
    search_vector = DocumentChunk.__table__.c.search_vector

    assert isinstance(search_text.type, Text)
    assert search_text.nullable is False
    assert search_text.default is not None
    assert search_text.default.arg == ""
    assert isinstance(search_vector.type, TSVECTOR)
    assert search_vector.computed is not None
    assert str(search_vector.computed.sqltext) == "to_tsvector('simple', search_text)"
    assert search_vector.computed.persisted is True


def test_document_job_declares_status_lease_error_and_scheduling_contract() -> None:
    columns = DocumentJob.__table__.c

    assert columns.max_attempts.default.arg == 3
    assert columns.lease_token.nullable is True
    assert columns.error_code.type.length == 50
    assert isinstance(columns.error_message.type, Text)
    assert {
        "run_after",
        "attempt_count",
        "max_attempts",
        "lease_owner",
        "lease_token",
        "lease_expires_at",
        "heartbeat_at",
    }.issubset(columns.keys())

    checks = [
        constraint
        for constraint in DocumentJob.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    ]
    assert any(
        "status IN ('pending', 'processing', 'retry_wait', 'succeeded', 'failed', 'canceled')"
        in str(constraint.sqltext)
        for constraint in checks
    )

    indexes = {
        index.name: index for index in DocumentJob.__table__.indexes if isinstance(index, Index)
    }
    assert {
        "ix_document_jobs_status_run_after",
        "ix_document_jobs_lease_expires_at",
        "uq_document_jobs_active_resource",
    }.issubset(indexes)
    assert indexes["uq_document_jobs_active_resource"].unique is True
    assert (
        str(indexes["uq_document_jobs_active_resource"].dialect_options["postgresql"]["where"])
        == "status IN ('pending', 'processing', 'retry_wait')"
    )


def test_worker_heartbeat_uses_worker_id_as_primary_key() -> None:
    assert WorkerHeartbeat.__table__.c.worker_id.primary_key is True
    assert {
        "worker_id",
        "status",
        "current_job_id",
        "last_seen_at",
    } == set(WorkerHeartbeat.__table__.c.keys())


def test_job_contracts_expose_fixed_types_and_lease_fields() -> None:
    assert set(get_args(JobType)) == {
        "ingest_document",
        "purge_document",
        "purge_knowledge_base",
    }
    assert set(get_args(JobStatus)) == {
        "pending",
        "processing",
        "retry_wait",
        "succeeded",
        "failed",
        "canceled",
    }
    assert [field.name for field in fields(JobLease)] == [
        "job_id",
        "job_type",
        "resource_type",
        "resource_id",
        "owner_user_id",
        "knowledge_base_id",
        "attempt_number",
        "lease_token",
        "lease_expires_at",
    ]

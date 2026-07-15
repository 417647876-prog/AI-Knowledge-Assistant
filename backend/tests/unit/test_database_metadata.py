from dataclasses import fields
from typing import get_args

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR

from app.db.base import Base
from app.db.models import (
    ADMIN_ROLE,
    USER_ROLE,
    AnswerFeedback,
    AnswerObservation,
    AuditEvent,
    Conversation,
    ConversationMessage,
    Document,
    DocumentChunk,
    DocumentJob,
    KnowledgeBase,
    LlmUsageEvent,
    QualityEvaluationRun,
    RefreshSession,
    SupportAccessGrant,
    User,
    UserQuota,
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
        "conversations",
        "conversation_messages",
        "llm_usage_events",
        "answer_observations",
        "answer_feedback",
        "user_quotas",
        "quality_evaluation_runs",
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
    assert Conversation.__tablename__ == "conversations"
    assert ConversationMessage.__tablename__ == "conversation_messages"
    assert LlmUsageEvent.__tablename__ == "llm_usage_events"
    assert AnswerObservation.__tablename__ == "answer_observations"
    assert AnswerFeedback.__tablename__ == "answer_feedback"
    assert UserQuota.__tablename__ == "user_quotas"
    assert QualityEvaluationRun.__tablename__ == "quality_evaluation_runs"


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


def _check_sql(model: type[Base]) -> set[str]:
    return {
        str(constraint.sqltext)
        for constraint in model.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }


def _index(model: type[Base], name: str) -> Index:
    return next(index for index in model.__table__.indexes if index.name == name)


def test_conversation_models_declare_private_streaming_contract() -> None:
    conversation_columns = Conversation.__table__.c
    assert {
        "id",
        "user_id",
        "knowledge_base_id",
        "title",
        "created_at",
        "updated_at",
    } == set(conversation_columns.keys())
    assert conversation_columns.user_id.nullable is False
    assert conversation_columns.knowledge_base_id.nullable is False
    assert _index(Conversation, "ix_conversations_knowledge_base_id")

    message_columns = ConversationMessage.__table__.c
    assert {
        "id",
        "conversation_id",
        "sequence_number",
        "role",
        "content",
        "status",
        "retry_of_message_id",
        "citations_snapshot",
        "retrieval_stats",
        "timings",
        "finish_reason",
        "error_code",
        "created_at",
        "completed_at",
    } == set(message_columns.keys())
    assert isinstance(message_columns.content.type, Text)
    assert isinstance(message_columns.citations_snapshot.type, JSONB)
    assert isinstance(message_columns.retrieval_stats.type, JSONB)
    assert isinstance(message_columns.timings.type, JSONB)

    checks = _check_sql(ConversationMessage)
    assert "sequence_number > 0" in checks
    assert "role IN ('user', 'assistant')" in checks
    assert "status IN ('streaming', 'completed', 'interrupted', 'failed')" in checks
    assert "role = 'assistant' OR status = 'completed'" in checks
    assert (
        "(status = 'streaming' AND completed_at IS NULL) OR "
        "(status <> 'streaming' AND completed_at IS NOT NULL)"
    ) in checks

    sequence_index = _index(ConversationMessage, "uq_conversation_messages_conversation_sequence")
    assert sequence_index.unique is True
    assert [column.name for column in sequence_index.columns] == [
        "conversation_id",
        "sequence_number",
    ]
    retry_foreign_key = next(iter(message_columns.retry_of_message_id.foreign_keys))
    assert retry_foreign_key.ondelete != "CASCADE"


def test_usage_model_uses_fixed_precision_and_non_cascading_resource_links() -> None:
    columns = LlmUsageEvent.__table__.c
    assert {
        "id",
        "user_id",
        "knowledge_base_id",
        "conversation_id",
        "message_id",
        "purpose",
        "status",
        "model",
        "provider_request_id",
        "cache_hit_input_tokens",
        "cache_miss_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
        "usage_complete",
        "price_snapshot",
        "reserved_cost",
        "settled_cost",
        "duration_ms",
        "finish_reason",
        "error_code",
        "created_at",
        "completed_at",
    } == set(columns.keys())
    assert isinstance(columns.reserved_cost.type, Numeric)
    assert isinstance(columns.settled_cost.type, Numeric)
    assert (columns.reserved_cost.type.precision, columns.reserved_cost.type.scale) == (20, 6)
    assert (columns.settled_cost.type.precision, columns.settled_cost.type.scale) == (20, 6)
    assert isinstance(columns.price_snapshot.type, JSONB)
    assert columns.message_id.nullable is False

    checks = _check_sql(LlmUsageEvent)
    assert "purpose IN ('rewrite', 'answer')" in checks
    assert (
        "status IN ('reserved', 'succeeded', 'usage_unknown', "
        "'failed_before_request', 'failed_after_request')"
    ) in checks
    assert {
        "cache_hit_input_tokens >= 0",
        "cache_miss_input_tokens >= 0",
        "output_tokens >= 0",
        "reasoning_tokens >= 0",
        "total_tokens >= 0",
        "total_tokens = cache_hit_input_tokens + cache_miss_input_tokens + output_tokens",
        "reserved_cost >= 0",
        "settled_cost IS NULL OR settled_cost >= 0",
        "duration_ms IS NULL OR duration_ms >= 0",
    }.issubset(checks)
    assert (
        "(status = 'succeeded' AND usage_complete) OR "
        "(status <> 'succeeded' AND NOT usage_complete)"
    ) in checks
    for column_name in ("user_id", "knowledge_base_id", "conversation_id", "message_id"):
        foreign_key = next(iter(columns[column_name].foreign_keys))
        assert foreign_key.ondelete != "CASCADE"


def test_quality_models_store_private_links_and_no_answer_body_copy() -> None:
    observation_columns = AnswerObservation.__table__.c
    assert {
        "id",
        "user_id",
        "knowledge_base_id",
        "conversation_id",
        "message_id",
        "was_rewritten",
        "rewrite_fallback",
        "candidate_count",
        "accepted_count",
        "max_relevance",
        "average_relevance",
        "refused",
        "citation_count",
        "citations_valid",
        "rewrite_ms",
        "retrieval_ms",
        "generation_ms",
        "total_ms",
        "finish_reason",
        "error_code",
        "created_at",
    } == set(observation_columns.keys())
    assert _index(AnswerObservation, "uq_answer_observations_message_id").unique is True
    assert _index(AnswerObservation, "ix_answer_observations_conversation_id")

    feedback_columns = AnswerFeedback.__table__.c
    assert {
        "id",
        "message_id",
        "user_id",
        "helpful",
        "reason",
        "created_at",
        "updated_at",
    } == set(feedback_columns.keys())
    feedback_index = _index(AnswerFeedback, "uq_answer_feedback_user_message")
    assert feedback_index.unique is True
    assert [column.name for column in feedback_index.columns] == ["user_id", "message_id"]

    forbidden_columns = {
        "question",
        "answer",
        "prompt",
        "document_content",
        "file_name",
        "knowledge_base_name",
    }
    summary_models = (
        LlmUsageEvent,
        AnswerObservation,
        AnswerFeedback,
        AuditEvent,
        UserQuota,
        QualityEvaluationRun,
    )
    for model in summary_models:
        assert forbidden_columns.isdisjoint(model.__table__.c.keys())

    private_models = (
        Conversation,
        ConversationMessage,
        LlmUsageEvent,
        AnswerObservation,
        AnswerFeedback,
        UserQuota,
    )
    for model in private_models:
        for foreign_key in model.__table__.foreign_keys:
            assert foreign_key.ondelete != "CASCADE"


def test_quota_and_offline_quality_models_use_safe_summary_types() -> None:
    quota_columns = UserQuota.__table__.c
    assert quota_columns.user_id.primary_key is True
    assert isinstance(quota_columns.storage_bytes_limit.type, BigInteger)
    assert {
        "daily_question_limit",
        "daily_upload_limit",
        "storage_bytes_limit",
        "current_count_date",
        "question_count",
        "upload_count",
        "created_at",
        "updated_at",
    }.issubset(quota_columns.keys())
    quota_checks = _check_sql(UserQuota)
    assert {
        "daily_question_limit IS NULL OR daily_question_limit >= 0",
        "daily_upload_limit IS NULL OR daily_upload_limit >= 0",
        "storage_bytes_limit IS NULL OR storage_bytes_limit >= 0",
        "question_count >= 0",
        "upload_count >= 0",
    }.issubset(quota_checks)

    evaluation_columns = QualityEvaluationRun.__table__.c
    assert {
        "id",
        "dataset_hash",
        "mode",
        "model_config_summary",
        "metrics",
        "report_hash",
        "gate_passed",
        "started_at",
        "completed_at",
        "duration_ms",
        "created_at",
    } == set(evaluation_columns.keys())
    assert isinstance(evaluation_columns.model_config_summary.type, JSONB)
    assert isinstance(evaluation_columns.metrics.type, JSONB)
    assert _index(QualityEvaluationRun, "ix_quality_evaluation_runs_completed_at")


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

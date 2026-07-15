"""建立会话、用量、质量和额度数据库契约

Revision ID: 20260716_08
Revises: 20260716_07
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260716_08"
down_revision: str | None = "20260716_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_scope_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION validate_conversation_scope() RETURNS trigger AS $$
        DECLARE
            expected_user_id uuid;
        BEGIN
            IF TG_OP = 'UPDATE' AND (
                NEW.user_id IS DISTINCT FROM OLD.user_id OR
                NEW.knowledge_base_id IS DISTINCT FROM OLD.knowledge_base_id
            ) THEN
                RAISE EXCEPTION 'conversation resource scope is immutable'
                    USING ERRCODE = '23514';
            END IF;

            SELECT owner_id INTO expected_user_id
            FROM knowledge_bases
            WHERE id = NEW.knowledge_base_id
            FOR SHARE;
            IF NOT FOUND OR expected_user_id IS DISTINCT FROM NEW.user_id THEN
                RAISE EXCEPTION 'conversation user does not own knowledge base'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_conversations_validate_scope
        BEFORE INSERT OR UPDATE ON conversations
        FOR EACH ROW EXECUTE FUNCTION validate_conversation_scope()
        """
    )
    op.execute(
        """
        CREATE FUNCTION preserve_conversation_knowledge_base_owner() RETURNS trigger AS $$
        BEGIN
            IF NEW.owner_id IS DISTINCT FROM OLD.owner_id AND EXISTS (
                SELECT 1 FROM conversations WHERE knowledge_base_id = OLD.id
            ) THEN
                RAISE EXCEPTION 'knowledge base owner is referenced by conversations'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_knowledge_bases_preserve_conversation_owner
        BEFORE UPDATE OF owner_id ON knowledge_bases
        FOR EACH ROW EXECUTE FUNCTION preserve_conversation_knowledge_base_owner()
        """
    )
    op.execute(
        """
        CREATE FUNCTION validate_conversation_message() RETURNS trigger AS $$
        DECLARE
            target_conversation_id uuid;
            target_role varchar(20);
        BEGIN
            IF TG_OP = 'UPDATE' AND (
                NEW.conversation_id IS DISTINCT FROM OLD.conversation_id OR
                NEW.sequence_number IS DISTINCT FROM OLD.sequence_number OR
                NEW.role IS DISTINCT FROM OLD.role
            ) THEN
                RAISE EXCEPTION 'message resource identity is immutable'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.retry_of_message_id IS NOT NULL THEN
                SELECT conversation_id, role
                INTO target_conversation_id, target_role
                FROM conversation_messages
                WHERE id = NEW.retry_of_message_id
                FOR KEY SHARE;
                IF NOT FOUND OR NEW.role <> 'assistant' OR target_role <> 'assistant'
                    OR target_conversation_id IS DISTINCT FROM NEW.conversation_id THEN
                    RAISE EXCEPTION 'retry target must be an assistant message in same conversation'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_conversation_messages_validate_retry
        BEFORE INSERT OR UPDATE ON conversation_messages
        FOR EACH ROW EXECUTE FUNCTION validate_conversation_message()
        """
    )
    op.execute(
        """
        CREATE FUNCTION validate_observability_scope() RETURNS trigger AS $$
        DECLARE
            expected_user_id uuid;
            expected_knowledge_base_id uuid;
            expected_conversation_id uuid;
            target_role varchar(20);
        BEGIN
            IF TG_OP = 'UPDATE' AND (
                NEW.user_id IS DISTINCT FROM OLD.user_id OR
                NEW.knowledge_base_id IS DISTINCT FROM OLD.knowledge_base_id OR
                NEW.conversation_id IS DISTINCT FROM OLD.conversation_id OR
                NEW.message_id IS DISTINCT FROM OLD.message_id
            ) THEN
                RAISE EXCEPTION 'observability resource scope is immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF TG_OP = 'UPDATE' THEN
                RETURN NEW;
            END IF;

            SELECT user_id, knowledge_base_id
            INTO expected_user_id, expected_knowledge_base_id
            FROM conversations
            WHERE id = NEW.conversation_id
            FOR KEY SHARE;
            IF NOT FOUND OR expected_user_id IS DISTINCT FROM NEW.user_id
                OR expected_knowledge_base_id IS DISTINCT FROM NEW.knowledge_base_id THEN
                RAISE EXCEPTION 'observability scope does not match conversation'
                    USING ERRCODE = '23514';
            END IF;

            SELECT conversation_id, role INTO expected_conversation_id, target_role
            FROM conversation_messages
            WHERE id = NEW.message_id
            FOR KEY SHARE;
            IF NOT FOUND OR expected_conversation_id IS DISTINCT FROM NEW.conversation_id
                OR target_role <> 'assistant' THEN
                RAISE EXCEPTION 'observability must belong to an assistant message'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table_name, trigger_name in (
        ("llm_usage_events", "trg_llm_usage_events_validate_scope"),
        ("answer_observations", "trg_answer_observations_validate_scope"),
    ):
        op.execute(
            f"""
            CREATE TRIGGER {trigger_name}
            BEFORE INSERT OR UPDATE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION validate_observability_scope()
            """
        )
    op.execute(
        """
        CREATE FUNCTION validate_answer_feedback_scope() RETURNS trigger AS $$
        DECLARE
            expected_user_id uuid;
            target_role varchar(20);
        BEGIN
            IF TG_OP = 'UPDATE' AND (
                NEW.user_id IS DISTINCT FROM OLD.user_id OR
                NEW.message_id IS DISTINCT FROM OLD.message_id
            ) THEN
                RAISE EXCEPTION 'feedback resource scope is immutable'
                    USING ERRCODE = '23514';
            END IF;

            SELECT conversations.user_id, conversation_messages.role
            INTO expected_user_id, target_role
            FROM conversation_messages
            JOIN conversations
              ON conversations.id = conversation_messages.conversation_id
            WHERE conversation_messages.id = NEW.message_id
            FOR KEY SHARE OF conversation_messages, conversations;
            IF NOT FOUND OR expected_user_id IS DISTINCT FROM NEW.user_id
                OR target_role <> 'assistant' THEN
                RAISE EXCEPTION 'feedback must belong to the answer owner'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_answer_feedback_validate_scope
        BEFORE INSERT OR UPDATE ON answer_feedback
        FOR EACH ROW EXECUTE FUNCTION validate_answer_feedback_scope()
        """
    )


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversations_user_updated_at", "conversations", ["user_id", "updated_at"])
    op.create_index("ix_conversations_knowledge_base_id", "conversations", ["knowledge_base_id"])
    op.create_index(
        "ix_conversations_user_knowledge_base_updated_at",
        "conversations",
        ["user_id", "knowledge_base_id", "updated_at"],
    )
    op.create_table(
        "conversation_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("retry_of_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "citations_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "retrieval_stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "timings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("finish_reason", sa.String(length=50), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "sequence_number > 0", name=op.f("ck_conversation_messages_sequence_number_positive")
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')", name=op.f("ck_conversation_messages_role_values")
        ),
        sa.CheckConstraint(
            "status IN ('streaming', 'completed', 'interrupted', 'failed')",
            name=op.f("ck_conversation_messages_status_values"),
        ),
        sa.CheckConstraint(
            "role = 'assistant' OR status = 'completed'",
            name=op.f("ck_conversation_messages_user_message_completed"),
        ),
        sa.CheckConstraint(
            "retry_of_message_id IS NULL OR role = 'assistant'",
            name=op.f("ck_conversation_messages_retry_only_for_assistant"),
        ),
        sa.CheckConstraint(
            "(status = 'streaming' AND completed_at IS NULL) OR "
            "(status <> 'streaming' AND completed_at IS NOT NULL)",
            name=op.f("ck_conversation_messages_completion_timestamp_matches_status"),
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["retry_of_message_id"], ["conversation_messages.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_conversation_messages_conversation_sequence",
        "conversation_messages",
        ["conversation_id", "sequence_number"],
        unique=True,
    )
    op.create_index(
        "ix_conversation_messages_retry_of_message_id",
        "conversation_messages",
        ["retry_of_message_id"],
    )
    op.create_table(
        "llm_usage_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("provider_request_id", sa.String(length=200), nullable=True),
        sa.Column("cache_hit_input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cache_miss_input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reasoning_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("usage_complete", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "price_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("reserved_cost", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("settled_cost", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("finish_reason", sa.String(length=50), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "purpose IN ('rewrite', 'answer')", name=op.f("ck_llm_usage_events_purpose_values")
        ),
        sa.CheckConstraint(
            "status IN ('reserved', 'succeeded', 'usage_unknown', "
            "'failed_before_request', 'failed_after_request')",
            name=op.f("ck_llm_usage_events_status_values"),
        ),
        sa.CheckConstraint(
            "cache_hit_input_tokens >= 0",
            name=op.f("ck_llm_usage_events_cache_hit_tokens_non_negative"),
        ),
        sa.CheckConstraint(
            "cache_miss_input_tokens >= 0",
            name=op.f("ck_llm_usage_events_cache_miss_tokens_non_negative"),
        ),
        sa.CheckConstraint(
            "output_tokens >= 0", name=op.f("ck_llm_usage_events_output_tokens_non_negative")
        ),
        sa.CheckConstraint(
            "reasoning_tokens >= 0", name=op.f("ck_llm_usage_events_reasoning_tokens_non_negative")
        ),
        sa.CheckConstraint(
            "reasoning_tokens <= output_tokens",
            name=op.f("ck_llm_usage_events_reasoning_tokens_within_output"),
        ),
        sa.CheckConstraint(
            "total_tokens >= 0", name=op.f("ck_llm_usage_events_total_tokens_non_negative")
        ),
        sa.CheckConstraint(
            "total_tokens = cache_hit_input_tokens + cache_miss_input_tokens + output_tokens",
            name=op.f("ck_llm_usage_events_total_tokens_match_components"),
        ),
        sa.CheckConstraint(
            "reserved_cost >= 0", name=op.f("ck_llm_usage_events_reserved_cost_non_negative")
        ),
        sa.CheckConstraint(
            "settled_cost IS NULL OR settled_cost >= 0",
            name=op.f("ck_llm_usage_events_settled_cost_non_negative"),
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name=op.f("ck_llm_usage_events_duration_non_negative"),
        ),
        sa.CheckConstraint(
            "(status = 'succeeded' AND usage_complete) OR "
            "status = 'failed_after_request' OR "
            "(status IN ('reserved', 'usage_unknown', 'failed_before_request') "
            "AND NOT usage_complete)",
            name=op.f("ck_llm_usage_events_usage_completeness_matches_status"),
        ),
        sa.CheckConstraint(
            "(status = 'reserved' AND completed_at IS NULL) OR "
            "(status <> 'reserved' AND completed_at IS NOT NULL)",
            name=op.f("ck_llm_usage_events_completion_timestamp_matches_status"),
        ),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_llm_usage_events_user_created_at", "llm_usage_events", ["user_id", "created_at"]
    )
    op.create_index(
        "ix_llm_usage_events_knowledge_base_id", "llm_usage_events", ["knowledge_base_id"]
    )
    op.create_index("ix_llm_usage_events_conversation_id", "llm_usage_events", ["conversation_id"])
    op.create_index("ix_llm_usage_events_message_id", "llm_usage_events", ["message_id"])
    op.create_index(
        "uq_llm_usage_events_provider_request_id",
        "llm_usage_events",
        ["provider_request_id"],
        unique=True,
        postgresql_where=sa.text("provider_request_id IS NOT NULL"),
    )
    op.create_table(
        "answer_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("was_rewritten", sa.Boolean(), nullable=False),
        sa.Column("rewrite_fallback", sa.Boolean(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("accepted_count", sa.Integer(), nullable=False),
        sa.Column("max_relevance", sa.Numeric(precision=8, scale=6), nullable=True),
        sa.Column("average_relevance", sa.Numeric(precision=8, scale=6), nullable=True),
        sa.Column("refused", sa.Boolean(), nullable=False),
        sa.Column("citation_count", sa.Integer(), nullable=False),
        sa.Column("citations_valid", sa.Boolean(), nullable=False),
        sa.Column("rewrite_ms", sa.Integer(), nullable=False),
        sa.Column("retrieval_ms", sa.Integer(), nullable=False),
        sa.Column("generation_ms", sa.Integer(), nullable=False),
        sa.Column("total_ms", sa.Integer(), nullable=False),
        sa.Column("finish_reason", sa.String(length=50), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "candidate_count >= 0", name=op.f("ck_answer_observations_candidate_count_non_negative")
        ),
        sa.CheckConstraint(
            "accepted_count >= 0", name=op.f("ck_answer_observations_accepted_count_non_negative")
        ),
        sa.CheckConstraint(
            "accepted_count <= candidate_count",
            name=op.f("ck_answer_observations_accepted_within_candidates"),
        ),
        sa.CheckConstraint(
            "max_relevance IS NULL OR (max_relevance >= 0 AND max_relevance <= 1)",
            name=op.f("ck_answer_observations_max_relevance_range"),
        ),
        sa.CheckConstraint(
            "average_relevance IS NULL OR (average_relevance >= 0 AND average_relevance <= 1)",
            name=op.f("ck_answer_observations_average_relevance_range"),
        ),
        sa.CheckConstraint(
            "citation_count >= 0", name=op.f("ck_answer_observations_citation_count_non_negative")
        ),
        sa.CheckConstraint(
            "rewrite_ms >= 0", name=op.f("ck_answer_observations_rewrite_ms_non_negative")
        ),
        sa.CheckConstraint(
            "retrieval_ms >= 0", name=op.f("ck_answer_observations_retrieval_ms_non_negative")
        ),
        sa.CheckConstraint(
            "generation_ms >= 0", name=op.f("ck_answer_observations_generation_ms_non_negative")
        ),
        sa.CheckConstraint(
            "total_ms >= 0", name=op.f("ck_answer_observations_total_ms_non_negative")
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"]),
        sa.ForeignKeyConstraint(["message_id"], ["conversation_messages.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_answer_observations_message_id", "answer_observations", ["message_id"], unique=True
    )
    op.create_index(
        "ix_answer_observations_user_created_at", "answer_observations", ["user_id", "created_at"]
    )
    op.create_index(
        "ix_answer_observations_knowledge_base_id", "answer_observations", ["knowledge_base_id"]
    )
    op.create_index(
        "ix_answer_observations_conversation_id", "answer_observations", ["conversation_id"]
    )
    op.create_table(
        "answer_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("helpful", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["message_id"], ["conversation_messages.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_answer_feedback_user_message", "answer_feedback", ["user_id", "message_id"], unique=True
    )
    op.create_index("ix_answer_feedback_message_id", "answer_feedback", ["message_id"])
    op.create_table(
        "user_quotas",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("daily_question_limit", sa.Integer(), nullable=True),
        sa.Column("daily_upload_limit", sa.Integer(), nullable=True),
        sa.Column("storage_bytes_limit", sa.BigInteger(), nullable=True),
        sa.Column(
            "current_count_date", sa.Date(), server_default=sa.text("CURRENT_DATE"), nullable=False
        ),
        sa.Column("question_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("upload_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "daily_question_limit IS NULL OR daily_question_limit >= 0",
            name=op.f("ck_user_quotas_daily_question_limit_non_negative"),
        ),
        sa.CheckConstraint(
            "daily_upload_limit IS NULL OR daily_upload_limit >= 0",
            name=op.f("ck_user_quotas_daily_upload_limit_non_negative"),
        ),
        sa.CheckConstraint(
            "storage_bytes_limit IS NULL OR storage_bytes_limit >= 0",
            name=op.f("ck_user_quotas_storage_bytes_limit_non_negative"),
        ),
        sa.CheckConstraint(
            "question_count >= 0", name=op.f("ck_user_quotas_question_count_non_negative")
        ),
        sa.CheckConstraint(
            "upload_count >= 0", name=op.f("ck_user_quotas_upload_count_non_negative")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "quality_evaluation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("mode", sa.String(length=30), nullable=False),
        sa.Column(
            "model_config_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("report_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("gate_passed", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "duration_ms >= 0", name=op.f("ck_quality_evaluation_runs_duration_non_negative")
        ),
        sa.CheckConstraint(
            "completed_at >= started_at",
            name=op.f("ck_quality_evaluation_runs_completion_after_start"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_hash", name=op.f("uq_quality_evaluation_runs_report_hash")),
    )
    op.create_index(
        "ix_quality_evaluation_runs_completed_at", "quality_evaluation_runs", ["completed_at"]
    )
    _create_scope_triggers()


def downgrade() -> None:
    bind = op.get_bind()
    op.execute(
        "LOCK TABLE conversations, conversation_messages, llm_usage_events, "
        "answer_observations, answer_feedback, user_quotas, quality_evaluation_runs "
        "IN SHARE ROW EXCLUSIVE MODE"
    )
    row_count = bind.scalar(
        sa.text(
            "SELECT (SELECT count(*) FROM conversations) + "
            "(SELECT count(*) FROM conversation_messages) + "
            "(SELECT count(*) FROM llm_usage_events) + "
            "(SELECT count(*) FROM answer_observations) + "
            "(SELECT count(*) FROM answer_feedback) + "
            "(SELECT count(*) FROM user_quotas) + "
            "(SELECT count(*) FROM quality_evaluation_runs)"
        )
    )
    if row_count:
        raise RuntimeError("会话、用量、质量或额度表存在新状态，不能无损降级")

    op.execute("DROP TRIGGER trg_answer_feedback_validate_scope ON answer_feedback")
    op.execute("DROP FUNCTION validate_answer_feedback_scope()")
    op.execute("DROP TRIGGER trg_answer_observations_validate_scope ON answer_observations")
    op.execute("DROP TRIGGER trg_llm_usage_events_validate_scope ON llm_usage_events")
    op.execute("DROP FUNCTION validate_observability_scope()")
    op.execute("DROP TRIGGER trg_conversation_messages_validate_retry ON conversation_messages")
    op.execute("DROP FUNCTION validate_conversation_message()")
    op.execute("DROP TRIGGER trg_conversations_validate_scope ON conversations")
    op.execute("DROP FUNCTION validate_conversation_scope()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_knowledge_bases_preserve_conversation_owner ON knowledge_bases"
    )
    op.execute("DROP FUNCTION IF EXISTS preserve_conversation_knowledge_base_owner()")

    op.drop_index("ix_quality_evaluation_runs_completed_at", table_name="quality_evaluation_runs")
    op.drop_table("quality_evaluation_runs")
    op.drop_table("user_quotas")
    op.drop_index("ix_answer_feedback_message_id", table_name="answer_feedback")
    op.drop_index("uq_answer_feedback_user_message", table_name="answer_feedback")
    op.drop_table("answer_feedback")
    op.execute("DROP INDEX IF EXISTS ix_answer_observations_conversation_id")
    op.drop_index("ix_answer_observations_knowledge_base_id", table_name="answer_observations")
    op.drop_index("ix_answer_observations_user_created_at", table_name="answer_observations")
    op.drop_index("uq_answer_observations_message_id", table_name="answer_observations")
    op.drop_table("answer_observations")
    op.drop_index("uq_llm_usage_events_provider_request_id", table_name="llm_usage_events")
    op.drop_index("ix_llm_usage_events_message_id", table_name="llm_usage_events")
    op.drop_index("ix_llm_usage_events_conversation_id", table_name="llm_usage_events")
    op.drop_index("ix_llm_usage_events_knowledge_base_id", table_name="llm_usage_events")
    op.drop_index("ix_llm_usage_events_user_created_at", table_name="llm_usage_events")
    op.drop_table("llm_usage_events")
    op.drop_index(
        "ix_conversation_messages_retry_of_message_id", table_name="conversation_messages"
    )
    op.drop_index(
        "uq_conversation_messages_conversation_sequence", table_name="conversation_messages"
    )
    op.drop_table("conversation_messages")
    op.execute("DROP INDEX IF EXISTS ix_conversations_knowledge_base_id")
    op.drop_index("ix_conversations_user_knowledge_base_updated_at", table_name="conversations")
    op.drop_index("ix_conversations_user_updated_at", table_name="conversations")
    op.drop_table("conversations")

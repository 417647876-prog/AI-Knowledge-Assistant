"""建立持久化文档任务、租约和 Worker 心跳契约

Revision ID: 20260716_06
Revises: 20260715_05
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260716_06"
down_revision: str | None = "20260715_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("ingestion_jobs", "document_jobs")
    op.execute("ALTER TABLE document_jobs RENAME CONSTRAINT pk_ingestion_jobs TO pk_document_jobs")

    op.add_column("document_jobs", sa.Column("job_type", sa.String(length=30), nullable=True))
    op.add_column("document_jobs", sa.Column("resource_type", sa.String(length=30), nullable=True))
    op.add_column(
        "document_jobs",
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "document_jobs",
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "document_jobs",
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "document_jobs",
        sa.Column(
            "run_after",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )
    op.add_column(
        "document_jobs",
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=True),
    )
    op.add_column(
        "document_jobs",
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=True),
    )
    op.add_column("document_jobs", sa.Column("lease_owner", sa.String(length=255), nullable=True))
    op.add_column(
        "document_jobs",
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "document_jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "document_jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.execute("UPDATE document_jobs SET status = 'processing' WHERE status = 'running'")
    op.execute(
        """
        UPDATE document_jobs AS jobs
        SET job_type = 'ingest_document',
            resource_type = 'document',
            resource_id = jobs.document_id,
            owner_user_id = knowledge_bases.owner_id,
            knowledge_base_id = documents.knowledge_base_id,
            run_after = COALESCE(jobs.created_at, now()),
            attempt_count = 0,
            max_attempts = 3
        FROM documents
        JOIN knowledge_bases ON knowledge_bases.id = documents.knowledge_base_id
        WHERE documents.id = jobs.document_id
        """
    )

    for column_name, column_type in (
        ("job_type", sa.String(length=30)),
        ("resource_type", sa.String(length=30)),
        ("resource_id", postgresql.UUID(as_uuid=True)),
        ("owner_user_id", postgresql.UUID(as_uuid=True)),
        ("knowledge_base_id", postgresql.UUID(as_uuid=True)),
        ("run_after", sa.DateTime(timezone=True)),
        ("attempt_count", sa.Integer()),
        ("max_attempts", sa.Integer()),
    ):
        op.alter_column(
            "document_jobs",
            column_name,
            existing_type=column_type,
            nullable=False,
        )

    op.alter_column(
        "document_jobs",
        "status",
        existing_type=sa.String(length=30),
        server_default=sa.text("'pending'"),
        nullable=False,
    )
    op.alter_column(
        "document_jobs",
        "stage",
        existing_type=sa.String(length=30),
        nullable=True,
    )
    op.alter_column(
        "document_jobs",
        "chunk_count",
        existing_type=sa.Integer(),
        server_default=sa.text("0"),
        nullable=False,
    )

    op.create_foreign_key(
        op.f("fk_document_jobs_owner_user_id_users"),
        "document_jobs",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("fk_document_jobs_knowledge_base_id_knowledge_bases"),
        "document_jobs",
        "knowledge_bases",
        ["knowledge_base_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        op.f("ck_document_jobs_job_type_values"),
        "document_jobs",
        "job_type IN ('ingest_document', 'purge_document', 'purge_knowledge_base')",
    )
    op.create_check_constraint(
        op.f("ck_document_jobs_resource_type_values"),
        "document_jobs",
        "resource_type IN ('document', 'knowledge_base')",
    )
    op.create_check_constraint(
        op.f("ck_document_jobs_status_values"),
        "document_jobs",
        "status IN ('pending', 'processing', 'retry_wait', 'succeeded', 'failed', 'canceled')",
    )
    op.create_check_constraint(
        op.f("ck_document_jobs_attempt_count_non_negative"),
        "document_jobs",
        "attempt_count >= 0",
    )
    op.create_check_constraint(
        op.f("ck_document_jobs_max_attempts_positive"),
        "document_jobs",
        "max_attempts > 0",
    )
    op.create_check_constraint(
        op.f("ck_document_jobs_chunk_count_non_negative"),
        "document_jobs",
        "chunk_count >= 0",
    )
    op.create_index(
        op.f("ix_document_jobs_status_run_after"),
        "document_jobs",
        ["status", "run_after"],
    )
    op.create_index(
        op.f("ix_document_jobs_lease_expires_at"),
        "document_jobs",
        ["lease_expires_at"],
    )
    op.create_index(
        op.f("ix_document_jobs_owner_user_id"),
        "document_jobs",
        ["owner_user_id"],
    )
    op.create_index(
        op.f("ix_document_jobs_knowledge_base_id"),
        "document_jobs",
        ["knowledge_base_id"],
    )
    op.create_index(
        op.f("uq_document_jobs_active_resource"),
        "document_jobs",
        ["resource_type", "resource_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'processing', 'retry_wait')"),
    )

    op.drop_index("ix_ingestion_jobs_document_id_created_at", table_name="document_jobs")
    op.drop_index("ix_ingestion_jobs_document_id", table_name="document_jobs")
    op.drop_constraint(
        "fk_ingestion_jobs_document_id_documents",
        "document_jobs",
        type_="foreignkey",
    )
    op.drop_column("document_jobs", "document_id")

    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("current_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["current_job_id"],
            ["document_jobs.id"],
            name=op.f("fk_worker_heartbeats_current_job_id_document_jobs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("worker_id", name=op.f("pk_worker_heartbeats")),
    )


def downgrade() -> None:
    connection = op.get_bind()
    non_ingestion_jobs = connection.scalar(
        sa.text("SELECT count(*) FROM document_jobs WHERE job_type <> 'ingest_document'")
    )
    if non_ingestion_jobs:
        raise RuntimeError(
            "document_jobs 中存在清理任务，不能无损降级为 ingestion_jobs；请先处理这些任务"
        )

    op.drop_table("worker_heartbeats")

    op.add_column(
        "document_jobs",
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.drop_constraint(
        op.f("ck_document_jobs_status_values"),
        "document_jobs",
        type_="check",
    )
    op.execute(
        """
        UPDATE document_jobs
        SET document_id = resource_id,
            status = CASE WHEN status = 'processing' THEN 'running' ELSE status END,
            stage = COALESCE(stage, 'parse')
        """
    )
    op.alter_column(
        "document_jobs",
        "document_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.alter_column(
        "document_jobs",
        "stage",
        existing_type=sa.String(length=30),
        nullable=False,
    )
    op.alter_column(
        "document_jobs",
        "status",
        existing_type=sa.String(length=30),
        server_default=None,
        nullable=False,
    )
    op.alter_column(
        "document_jobs",
        "chunk_count",
        existing_type=sa.Integer(),
        server_default=None,
        nullable=False,
    )

    op.drop_index(op.f("uq_document_jobs_active_resource"), table_name="document_jobs")
    op.drop_index(op.f("ix_document_jobs_knowledge_base_id"), table_name="document_jobs")
    op.drop_index(op.f("ix_document_jobs_owner_user_id"), table_name="document_jobs")
    op.drop_index(op.f("ix_document_jobs_lease_expires_at"), table_name="document_jobs")
    op.drop_index(op.f("ix_document_jobs_status_run_after"), table_name="document_jobs")
    for constraint_name in (
        "ck_document_jobs_chunk_count_non_negative",
        "ck_document_jobs_max_attempts_positive",
        "ck_document_jobs_attempt_count_non_negative",
        "ck_document_jobs_resource_type_values",
        "ck_document_jobs_job_type_values",
    ):
        op.drop_constraint(op.f(constraint_name), "document_jobs", type_="check")
    op.drop_constraint(
        op.f("fk_document_jobs_knowledge_base_id_knowledge_bases"),
        "document_jobs",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_document_jobs_owner_user_id_users"),
        "document_jobs",
        type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk_ingestion_jobs_document_id_documents"),
        "document_jobs",
        "documents",
        ["document_id"],
        ["id"],
        ondelete="CASCADE",
    )

    for column_name in (
        "heartbeat_at",
        "lease_expires_at",
        "lease_token",
        "lease_owner",
        "max_attempts",
        "attempt_count",
        "run_after",
        "knowledge_base_id",
        "owner_user_id",
        "resource_id",
        "resource_type",
        "job_type",
    ):
        op.drop_column("document_jobs", column_name)

    op.execute("ALTER TABLE document_jobs RENAME CONSTRAINT pk_document_jobs TO pk_ingestion_jobs")
    op.rename_table("document_jobs", "ingestion_jobs")
    op.create_index(
        op.f("ix_ingestion_jobs_document_id"),
        "ingestion_jobs",
        ["document_id"],
    )
    op.create_index(
        op.f("ix_ingestion_jobs_document_id_created_at"),
        "ingestion_jobs",
        ["document_id", "created_at"],
    )

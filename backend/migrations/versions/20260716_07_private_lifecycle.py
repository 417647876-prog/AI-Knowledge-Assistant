"""增加私有数据生命周期、临时支持授权和安全审计契约

Revision ID: 20260716_07
Revises: 20260716_06
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260716_07"
down_revision: str | None = "20260716_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace_business_foreign_keys(*, ondelete: str | None) -> None:
    foreign_keys = (
        (
            "fk_documents_knowledge_base_id_knowledge_bases",
            "documents",
            "knowledge_bases",
            ["knowledge_base_id"],
            ["id"],
        ),
        (
            "fk_document_chunks_document_id_documents",
            "document_chunks",
            "documents",
            ["document_id"],
            ["id"],
        ),
        (
            "fk_document_chunks_knowledge_base_id_knowledge_bases",
            "document_chunks",
            "knowledge_bases",
            ["knowledge_base_id"],
            ["id"],
        ),
        (
            "fk_document_jobs_knowledge_base_id_knowledge_bases",
            "document_jobs",
            "knowledge_bases",
            ["knowledge_base_id"],
            ["id"],
        ),
    )
    for constraint_name, source, target, local_columns, remote_columns in foreign_keys:
        op.drop_constraint(constraint_name, source, type_="foreignkey")
        op.create_foreign_key(
            constraint_name,
            source,
            target,
            local_columns,
            remote_columns,
            ondelete=ondelete,
        )


def upgrade() -> None:
    op.add_column(
        "knowledge_bases",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "knowledge_bases",
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("uploaded_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True),
    )

    op.execute(
        """
        UPDATE documents AS documents
        SET uploaded_by_user_id = knowledge_bases.owner_id
        FROM knowledge_bases
        WHERE knowledge_bases.id = documents.knowledge_base_id
          AND documents.uploaded_by_user_id IS NULL
        """
    )
    missing_uploader_count = op.get_bind().scalar(
        sa.text("SELECT count(*) FROM documents WHERE uploaded_by_user_id IS NULL")
    )
    if missing_uploader_count:
        raise RuntimeError(
            "documents 中存在无法从知识库所属用户回填的上传者，迁移不会删除或猜测数据"
        )
    op.alter_column(
        "documents",
        "uploaded_by_user_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.create_foreign_key(
        op.f("fk_documents_uploaded_by_user_id_users"),
        "documents",
        "users",
        ["uploaded_by_user_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_documents_uploaded_by_user_id"),
        "documents",
        ["uploaded_by_user_id"],
    )

    op.drop_constraint(
        "uq_documents_knowledge_base_id_file_hash",
        "documents",
        type_="unique",
    )
    op.create_index(
        "uq_documents_active_knowledge_base_file_hash",
        "documents",
        ["knowledge_base_id", "file_hash"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    _replace_business_foreign_keys(ondelete=None)

    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table(
        "support_access_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("admin_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "access_level",
            sa.String(length=20),
            server_default="read_only",
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "access_level = 'read_only'",
            name=op.f("ck_support_access_grants_access_level_read_only"),
        ),
        sa.CheckConstraint(
            "owner_user_id <> admin_user_id",
            name=op.f("ck_support_access_grants_owner_differs_from_admin"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_support_access_grants_expires_after_creation"),
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            name=op.f("fk_support_access_grants_knowledge_base_id_knowledge_bases"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name=op.f("fk_support_access_grants_owner_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["admin_user_id"],
            ["users.id"],
            name=op.f("fk_support_access_grants_admin_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_support_access_grants")),
    )
    op.create_index(
        op.f("ix_support_access_grants_knowledge_base_id"),
        "support_access_grants",
        ["knowledge_base_id"],
    )
    op.create_index(
        op.f("ix_support_access_grants_owner_user_id"),
        "support_access_grants",
        ["owner_user_id"],
    )
    op.create_index(
        op.f("ix_support_access_grants_admin_user_id"),
        "support_access_grants",
        ["admin_user_id"],
    )
    op.create_index(
        op.f("ix_support_access_grants_expires_at"),
        "support_access_grants",
        ["expires_at"],
    )
    op.execute(
        """
        ALTER TABLE support_access_grants
        ADD CONSTRAINT ex_support_access_grants_unrevoked_period
        EXCLUDE USING gist (
            knowledge_base_id WITH =,
            admin_user_id WITH =,
            tstzrange(created_at, expires_at, '[)') WITH &&
        ) WHERE (revoked_at IS NULL)
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_support_access_grant_scope()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            selected_admin_role text;
            selected_owner_id uuid;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                NEW.created_at := clock_timestamp();
            END IF;

            IF TG_OP = 'UPDATE'
               AND NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'support access creation time is immutable'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_support_access_grants_created_at_immutable';
            END IF;

            IF TG_OP = 'UPDATE'
               AND OLD.revoked_at IS NOT NULL
               AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
                RAISE EXCEPTION 'revoked support access cannot be reactivated or moved'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_support_access_grants_revocation_one_way';
            END IF;

            IF TG_OP = 'INSERT'
               OR (NEW.revoked_at IS NULL AND NEW.expires_at > clock_timestamp()) THEN
                SELECT role INTO selected_admin_role
                FROM users
                WHERE id = NEW.admin_user_id
                FOR SHARE;
                IF selected_admin_role IS DISTINCT FROM 'admin' THEN
                    RAISE EXCEPTION 'support access can only be granted to an admin'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'ck_support_access_grants_admin_role';
                END IF;

                SELECT owner_id INTO selected_owner_id
                FROM knowledge_bases
                WHERE id = NEW.knowledge_base_id
                FOR SHARE;
                IF selected_owner_id IS DISTINCT FROM NEW.owner_user_id THEN
                    RAISE EXCEPTION 'support access owner must own the knowledge base'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'ck_support_access_grants_knowledge_base_owner';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_support_access_grants_validate_scope
        BEFORE INSERT OR UPDATE
        ON support_access_grants
        FOR EACH ROW
        EXECUTE FUNCTION enforce_support_access_grant_scope()
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_active_support_admin_role_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.role = 'admin'
               AND NEW.role IS DISTINCT FROM 'admin'
               AND EXISTS (
                   SELECT 1
                   FROM support_access_grants
                   WHERE admin_user_id = OLD.id
                     AND revoked_at IS NULL
                     AND created_at <= clock_timestamp()
                     AND expires_at > clock_timestamp()
               ) THEN
                RAISE EXCEPTION 'an admin with active support access cannot change role'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_support_access_grants_admin_role';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_users_preserve_active_support_admin_role
        BEFORE UPDATE OF role
        ON users
        FOR EACH ROW
        EXECUTE FUNCTION prevent_active_support_admin_role_change()
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_active_support_owner_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.owner_id IS DISTINCT FROM NEW.owner_id
               AND EXISTS (
                   SELECT 1
                   FROM support_access_grants
                   WHERE knowledge_base_id = OLD.id
                     AND revoked_at IS NULL
                     AND created_at <= clock_timestamp()
                     AND expires_at > clock_timestamp()
               ) THEN
                RAISE EXCEPTION 'a knowledge base with active support access cannot change owner'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_support_access_grants_knowledge_base_owner';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_knowledge_bases_preserve_active_support_owner
        BEFORE UPDATE OF owner_id
        ON knowledge_bases
        FOR EACH ROW
        EXECUTE FUNCTION prevent_active_support_owner_change()
        """
    )

    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=50), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("result", sa.String(length=30), nullable=False),
        sa.Column(
            "security_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("request_id", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_audit_events_actor_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(op.f("ix_audit_events_actor_user_id"), "audit_events", ["actor_user_id"])
    op.create_index(
        op.f("ix_audit_events_resource"),
        "audit_events",
        ["resource_type", "resource_id"],
    )
    op.create_index(op.f("ix_audit_events_created_at"), "audit_events", ["created_at"])
    op.create_index(op.f("ix_audit_events_request_id"), "audit_events", ["request_id"])


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE knowledge_bases, documents, support_access_grants "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    recycle_state_count = connection.scalar(
        sa.text(
            "SELECT "
            "(SELECT count(*) FROM knowledge_bases "
            " WHERE deleted_at IS NOT NULL OR purge_after IS NOT NULL) + "
            "(SELECT count(*) FROM documents "
            " WHERE deleted_at IS NOT NULL OR purge_after IS NOT NULL)"
        )
    )
    active_grant_count = connection.scalar(
        sa.text(
            "SELECT count(*) FROM support_access_grants "
            "WHERE revoked_at IS NULL "
            "AND created_at <= clock_timestamp() AND expires_at > clock_timestamp()"
        )
    )
    if recycle_state_count or active_grant_count:
        raise RuntimeError(
            "数据库中存在回收站资源或有效临时授权，不能无损降级；"
            "请先恢复/永久清理资源并撤销有效授权"
        )

    op.drop_index(op.f("ix_audit_events_request_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_created_at"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_resource"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_actor_user_id"), table_name="audit_events")
    op.drop_table("audit_events")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_knowledge_bases_preserve_active_support_owner "
        "ON knowledge_bases"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_active_support_owner_change()")
    op.execute("DROP TRIGGER IF EXISTS trg_users_preserve_active_support_admin_role ON users")
    op.execute("DROP FUNCTION IF EXISTS prevent_active_support_admin_role_change()")
    op.execute("DROP TRIGGER trg_support_access_grants_validate_scope ON support_access_grants")
    op.execute("DROP FUNCTION enforce_support_access_grant_scope()")
    op.execute(
        "ALTER TABLE support_access_grants "
        "DROP CONSTRAINT ex_support_access_grants_unrevoked_period"
    )
    op.drop_index(op.f("ix_support_access_grants_expires_at"), table_name="support_access_grants")
    op.drop_index(
        op.f("ix_support_access_grants_admin_user_id"),
        table_name="support_access_grants",
    )
    op.drop_index(
        op.f("ix_support_access_grants_owner_user_id"),
        table_name="support_access_grants",
    )
    op.drop_index(
        op.f("ix_support_access_grants_knowledge_base_id"),
        table_name="support_access_grants",
    )
    op.drop_table("support_access_grants")

    _replace_business_foreign_keys(ondelete="CASCADE")
    op.drop_index("uq_documents_active_knowledge_base_file_hash", table_name="documents")
    op.create_unique_constraint(
        "uq_documents_knowledge_base_id_file_hash",
        "documents",
        ["knowledge_base_id", "file_hash"],
    )
    op.drop_index(op.f("ix_documents_uploaded_by_user_id"), table_name="documents")
    op.drop_constraint(
        op.f("fk_documents_uploaded_by_user_id_users"),
        "documents",
        type_="foreignkey",
    )
    op.drop_column("documents", "purge_after")
    op.drop_column("documents", "deleted_at")
    op.drop_column("documents", "uploaded_by_user_id")
    op.drop_column("knowledge_bases", "purge_after")
    op.drop_column("knowledge_bases", "deleted_at")

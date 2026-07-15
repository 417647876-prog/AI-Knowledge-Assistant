"""为文档入库任务增加创建时间

Revision ID: 20260714_04
Revises: 20260713_03
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260714_04"
down_revision: str | None = "20260713_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ingestion_jobs",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE ingestion_jobs
        SET created_at = COALESCE(started_at, finished_at, now())
        WHERE created_at IS NULL
        """
    )
    op.alter_column(
        "ingestion_jobs",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    op.create_index(
        "ix_ingestion_jobs_document_id_created_at",
        "ingestion_jobs",
        ["document_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ingestion_jobs_document_id_created_at",
        table_name="ingestion_jobs",
    )
    op.drop_column("ingestion_jobs", "created_at")

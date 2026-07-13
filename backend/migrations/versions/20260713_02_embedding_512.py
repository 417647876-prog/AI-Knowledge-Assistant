"""change document chunk embeddings to 512 dimensions

Revision ID: 20260713_02
Revises: 20260710_01
Create Date: 2026-07-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260713_02"
down_revision: str | None = "20260710_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM document_chunks")
    op.execute(
        "UPDATE documents SET status = 'pending', error_code = NULL, error_message = NULL"
    )
    op.execute("ALTER TABLE document_chunks ALTER COLUMN embedding TYPE vector(512)")


def downgrade() -> None:
    op.execute("DELETE FROM document_chunks")
    op.execute("ALTER TABLE document_chunks ALTER COLUMN embedding TYPE vector(1536)")

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DocumentJob(Base):
    __tablename__ = "document_jobs"
    __table_args__ = (
        CheckConstraint(
            "job_type IN ('ingest_document', 'purge_document', 'purge_knowledge_base')",
            name="job_type_values",
        ),
        CheckConstraint(
            "resource_type IN ('document', 'knowledge_base')",
            name="resource_type_values",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'retry_wait', 'succeeded', 'failed', 'canceled')",
            name="status_values",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        CheckConstraint("max_attempts > 0", name="max_attempts_positive"),
        CheckConstraint("chunk_count >= 0", name="chunk_count_non_negative"),
        Index("ix_document_jobs_status_run_after", "status", "run_after"),
        Index("ix_document_jobs_lease_expires_at", "lease_expires_at"),
        Index("ix_document_jobs_owner_user_id", "owner_user_id"),
        Index("ix_document_jobs_knowledge_base_id", "knowledge_base_id"),
        Index(
            "uq_document_jobs_active_resource",
            "resource_type",
            "resource_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'processing', 'retry_wait')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    job_type: Mapped[str] = mapped_column(String(30), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(30), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_token: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stage: Mapped[str | None] = mapped_column(String(30), nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

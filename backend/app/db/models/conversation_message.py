from datetime import datetime
from typing import Any
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        CheckConstraint("sequence_number > 0", name="sequence_number_positive"),
        CheckConstraint("role IN ('user', 'assistant')", name="role_values"),
        CheckConstraint(
            "status IN ('streaming', 'completed', 'interrupted', 'failed')",
            name="status_values",
        ),
        CheckConstraint(
            "role = 'assistant' OR status = 'completed'",
            name="user_message_completed",
        ),
        CheckConstraint(
            "retry_of_message_id IS NULL OR role = 'assistant'",
            name="retry_only_for_assistant",
        ),
        CheckConstraint(
            "(status = 'streaming' AND completed_at IS NULL) OR "
            "(status <> 'streaming' AND completed_at IS NOT NULL)",
            name="completion_timestamp_matches_status",
        ),
        Index(
            "uq_conversation_messages_conversation_sequence",
            "conversation_id",
            "sequence_number",
            unique=True,
        ),
        Index("ix_conversation_messages_retry_of_message_id", "retry_of_message_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    retry_of_message_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("conversation_messages.id"), nullable=True
    )
    citations_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    retrieval_stats: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    timings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    finish_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

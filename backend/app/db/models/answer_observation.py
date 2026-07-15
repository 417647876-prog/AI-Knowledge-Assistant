from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AnswerObservation(Base):
    __tablename__ = "answer_observations"
    __table_args__ = (
        CheckConstraint("candidate_count >= 0", name="candidate_count_non_negative"),
        CheckConstraint("accepted_count >= 0", name="accepted_count_non_negative"),
        CheckConstraint("accepted_count <= candidate_count", name="accepted_within_candidates"),
        CheckConstraint(
            "max_relevance IS NULL OR (max_relevance >= 0 AND max_relevance <= 1)",
            name="max_relevance_range",
        ),
        CheckConstraint(
            "average_relevance IS NULL OR (average_relevance >= 0 AND average_relevance <= 1)",
            name="average_relevance_range",
        ),
        CheckConstraint("citation_count >= 0", name="citation_count_non_negative"),
        CheckConstraint("rewrite_ms >= 0", name="rewrite_ms_non_negative"),
        CheckConstraint("retrieval_ms >= 0", name="retrieval_ms_non_negative"),
        CheckConstraint("generation_ms >= 0", name="generation_ms_non_negative"),
        CheckConstraint("total_ms >= 0", name="total_ms_non_negative"),
        Index("uq_answer_observations_message_id", "message_id", unique=True),
        Index("ix_answer_observations_user_created_at", "user_id", "created_at"),
        Index("ix_answer_observations_knowledge_base_id", "knowledge_base_id"),
        Index("ix_answer_observations_conversation_id", "conversation_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("knowledge_bases.id"), nullable=False
    )
    conversation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False
    )
    message_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("conversation_messages.id"), nullable=False
    )
    was_rewritten: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rewrite_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_relevance: Mapped[Decimal | None] = mapped_column(Numeric(8, 6), nullable=True)
    average_relevance: Mapped[Decimal | None] = mapped_column(Numeric(8, 6), nullable=True)
    refused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    citation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    citations_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rewrite_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retrieval_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    generation_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    finish_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

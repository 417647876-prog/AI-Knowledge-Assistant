from uuid import UUID, uuid4

from sqlalchemy import Boolean, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AnswerFeedback(TimestampMixin, Base):
    __tablename__ = "answer_feedback"
    __table_args__ = (
        Index("uq_answer_feedback_user_message", "user_id", "message_id", unique=True),
        Index("ix_answer_feedback_message_id", "message_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    message_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("conversation_messages.id"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    helpful: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

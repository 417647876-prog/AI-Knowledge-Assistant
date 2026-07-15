from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_user_updated_at", "user_id", "updated_at"),
        Index("ix_conversations_knowledge_base_id", "knowledge_base_id"),
        Index(
            "ix_conversations_user_knowledge_base_updated_at",
            "user_id",
            "knowledge_base_id",
            "updated_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("knowledge_bases.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)

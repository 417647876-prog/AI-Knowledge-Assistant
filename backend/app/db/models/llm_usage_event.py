from datetime import datetime
from decimal import Decimal
from typing import Any
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LlmUsageEvent(Base):
    __tablename__ = "llm_usage_events"
    __table_args__ = (
        CheckConstraint("purpose IN ('rewrite', 'answer')", name="purpose_values"),
        CheckConstraint(
            "status IN ('reserved', 'succeeded', 'usage_unknown', "
            "'failed_before_request', 'failed_after_request')",
            name="status_values",
        ),
        CheckConstraint("cache_hit_input_tokens >= 0", name="cache_hit_tokens_non_negative"),
        CheckConstraint("cache_miss_input_tokens >= 0", name="cache_miss_tokens_non_negative"),
        CheckConstraint("output_tokens >= 0", name="output_tokens_non_negative"),
        CheckConstraint("reasoning_tokens >= 0", name="reasoning_tokens_non_negative"),
        CheckConstraint("reasoning_tokens <= output_tokens", name="reasoning_tokens_within_output"),
        CheckConstraint("total_tokens >= 0", name="total_tokens_non_negative"),
        CheckConstraint(
            "total_tokens = cache_hit_input_tokens + cache_miss_input_tokens + output_tokens",
            name="total_tokens_match_components",
        ),
        CheckConstraint("reserved_cost >= 0", name="reserved_cost_non_negative"),
        CheckConstraint(
            "settled_cost IS NULL OR settled_cost >= 0", name="settled_cost_non_negative"
        ),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_non_negative"),
        CheckConstraint(
            "(status = 'succeeded' AND usage_complete) OR "
            "status = 'failed_after_request' OR "
            "(status IN ('reserved', 'usage_unknown', 'failed_before_request') "
            "AND NOT usage_complete)",
            name="usage_completeness_matches_status",
        ),
        CheckConstraint(
            "(status = 'reserved' AND completed_at IS NULL) OR "
            "(status <> 'reserved' AND completed_at IS NOT NULL)",
            name="completion_timestamp_matches_status",
        ),
        Index("ix_llm_usage_events_user_created_at", "user_id", "created_at"),
        Index("ix_llm_usage_events_knowledge_base_id", "knowledge_base_id"),
        Index("ix_llm_usage_events_conversation_id", "conversation_id"),
        Index("ix_llm_usage_events_message_id", "message_id"),
        Index(
            "uq_llm_usage_events_provider_request_id",
            "provider_request_id",
            unique=True,
            postgresql_where=text("provider_request_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("knowledge_bases.id"), nullable=False
    )
    conversation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    message_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cache_hit_input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    cache_miss_input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    reasoning_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    usage_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    price_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    reserved_cost: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    settled_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

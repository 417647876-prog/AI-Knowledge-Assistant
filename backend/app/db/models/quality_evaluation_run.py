from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CHAR, Boolean, CheckConstraint, DateTime, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class QualityEvaluationRun(Base):
    __tablename__ = "quality_evaluation_runs"
    __table_args__ = (
        CheckConstraint("duration_ms >= 0", name="duration_non_negative"),
        CheckConstraint("completed_at >= started_at", name="completion_after_start"),
        Index("ix_quality_evaluation_runs_completed_at", "completed_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    dataset_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(30), nullable=False)
    model_config_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    report_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    gate_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

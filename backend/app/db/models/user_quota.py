from datetime import date
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, Date, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class UserQuota(TimestampMixin, Base):
    __tablename__ = "user_quotas"
    __table_args__ = (
        CheckConstraint(
            "daily_question_limit IS NULL OR daily_question_limit >= 0",
            name="daily_question_limit_non_negative",
        ),
        CheckConstraint(
            "daily_upload_limit IS NULL OR daily_upload_limit >= 0",
            name="daily_upload_limit_non_negative",
        ),
        CheckConstraint(
            "storage_bytes_limit IS NULL OR storage_bytes_limit >= 0",
            name="storage_bytes_limit_non_negative",
        ),
        CheckConstraint("question_count >= 0", name="question_count_non_negative"),
        CheckConstraint("upload_count >= 0", name="upload_count_non_negative"),
    )

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )
    daily_question_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_upload_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_bytes_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    current_count_date: Mapped[date] = mapped_column(
        Date, nullable=False, server_default=func.current_date()
    )
    question_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    upload_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

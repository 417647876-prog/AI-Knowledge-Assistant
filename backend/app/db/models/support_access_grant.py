from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

READ_ONLY_ACCESS = "read_only"


class SupportAccessGrant(Base):
    __tablename__ = "support_access_grants"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("knowledge_bases.id"), nullable=False
    )
    owner_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    admin_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    access_level: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=READ_ONLY_ACCESS,
        server_default=READ_ONLY_ACCESS,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("access_level = 'read_only'", name="access_level_read_only"),
        CheckConstraint("owner_user_id <> admin_user_id", name="owner_differs_from_admin"),
        CheckConstraint("expires_at > created_at", name="expires_after_creation"),
        Index("ix_support_access_grants_knowledge_base_id", "knowledge_base_id"),
        Index("ix_support_access_grants_owner_user_id", "owner_user_id"),
        Index("ix_support_access_grants_admin_user_id", "admin_user_id"),
        Index("ix_support_access_grants_expires_at", "expires_at"),
        ExcludeConstraint(
            ("knowledge_base_id", "="),
            ("admin_user_id", "="),
            (func.tstzrange(created_at, expires_at, "[)"), "&&"),
            where=text("revoked_at IS NULL"),
            using="gist",
            name="ex_support_access_grants_unrevoked_period",
        ),
    )

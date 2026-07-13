from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db.base import Base, TimestampMixin

ADMIN_ROLE = "admin"
USER_ROLE = "user"
UserRole = Literal["admin", "user"]


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'user')", name="role_values"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[UserRole] = mapped_column(
        String(20), nullable=False, default=USER_ROLE, server_default=USER_ROLE
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    @validates("username")
    def normalize_username(self, _key: str, value: str) -> str:
        return value.strip().casefold()

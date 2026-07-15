from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_context import get_request_id
from app.db.models import AuditEvent
from app.db.session import session_factory


def add_audit_event(
    session: AsyncSession,
    *,
    actor_user_id: UUID | None,
    action: str,
    resource_type: str,
    resource_id: UUID | None,
    result: str,
    security_summary: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> AuditEvent:
    event = AuditEvent(
        actor_user_id=actor_user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        result=result,
        security_summary=dict(security_summary or {}),
        request_id=get_request_id() if request_id is None else request_id,
    )
    session.add(event)
    return event


async def record_denied_audit_event(
    *,
    actor_user_id: UUID | None,
    action: str,
    resource_type: str,
    resource_id: UUID | None,
    result: str = "denied",
    security_summary: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> None:
    """用独立短事务持久化拒绝，不提交或回滚调用方的未完成事务。"""
    async with session_factory.begin() as audit_session:
        add_audit_event(
            audit_session,
            actor_user_id=actor_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            result=result,
            security_summary=security_summary,
            request_id=request_id,
        )

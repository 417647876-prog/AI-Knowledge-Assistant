from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import add_audit_event, record_denied_audit_event
from app.core.exceptions import AppError
from app.db.models import (
    ADMIN_ROLE,
    Document,
    KnowledgeBase,
    SupportAccessGrant,
    User,
)


async def get_database_now(session: AsyncSession) -> datetime:
    now = await session.scalar(select(func.clock_timestamp()))
    if now is None:  # pragma: no cover - PostgreSQL always returns a timestamp
        raise RuntimeError("数据库未返回当前时间。")
    return now


def _active_grant_conditions(*, knowledge_base_id: UUID, admin_user_id: UUID) -> tuple[object, ...]:
    return (
        SupportAccessGrant.knowledge_base_id == knowledge_base_id,
        SupportAccessGrant.admin_user_id == admin_user_id,
        SupportAccessGrant.owner_user_id == KnowledgeBase.owner_id,
        SupportAccessGrant.access_level == "read_only",
        SupportAccessGrant.revoked_at.is_(None),
        KnowledgeBase.deleted_at.is_(None),
    )


def _not_found(resource_type: str) -> AppError:
    if resource_type == "document":
        return AppError(code="DOCUMENT_NOT_FOUND", message="文档不存在。", status_code=404)
    return AppError(code="KNOWLEDGE_BASE_NOT_FOUND", message="知识库不存在。", status_code=404)


async def _record_access_denied(
    *, current_user: User, resource_type: str, resource_id: UUID, reason: str
) -> None:
    await record_denied_audit_event(
        actor_user_id=current_user.id,
        action="support_access_denied",
        resource_type=resource_type,
        resource_id=resource_id,
        security_summary={"access_level": "read_only", "reason": reason},
    )


async def _consume_locked_grant(
    session: AsyncSession,
    *,
    current_admin: User,
    grant: SupportAccessGrant,
    resource_type: str,
    resource_id: UUID,
) -> None:
    now = await get_database_now(session)
    if grant.created_at > now or grant.expires_at <= now:
        await _record_access_denied(
            current_user=current_admin,
            resource_type=resource_type,
            resource_id=resource_id,
            reason="grant_unavailable",
        )
        raise _not_found(resource_type)

    if grant.last_used_at is None or now > grant.last_used_at:
        grant.last_used_at = now
    add_audit_event(
        session,
        actor_user_id=current_admin.id,
        action="support_access_used",
        resource_type=resource_type,
        resource_id=resource_id,
        result="success",
        security_summary={"access_level": "read_only"},
    )


async def get_supported_knowledge_base(
    session: AsyncSession,
    current_admin: User,
    knowledge_base_id: UUID,
) -> KnowledgeBase:
    if current_admin.role != ADMIN_ROLE:
        await _record_access_denied(
            current_user=current_admin,
            resource_type="knowledge_base",
            resource_id=knowledge_base_id,
            reason="role_mismatch",
        )
        raise _not_found("knowledge_base")

    row = (
        await session.execute(
            select(KnowledgeBase, SupportAccessGrant)
            .join(
                SupportAccessGrant,
                SupportAccessGrant.knowledge_base_id == KnowledgeBase.id,
            )
            .where(
                KnowledgeBase.id == knowledge_base_id,
                *_active_grant_conditions(
                    knowledge_base_id=knowledge_base_id,
                    admin_user_id=current_admin.id,
                ),
            )
            .order_by(SupportAccessGrant.created_at.desc(), SupportAccessGrant.id.desc())
            .limit(1)
            .with_for_update(of=SupportAccessGrant)
        )
    ).one_or_none()
    if row is None:
        await _record_access_denied(
            current_user=current_admin,
            resource_type="knowledge_base",
            resource_id=knowledge_base_id,
            reason="grant_unavailable",
        )
        raise _not_found("knowledge_base")

    knowledge_base, grant = row
    await _consume_locked_grant(
        session,
        current_admin=current_admin,
        grant=grant,
        resource_type="knowledge_base",
        resource_id=knowledge_base.id,
    )
    return knowledge_base


async def get_supported_document(
    session: AsyncSession,
    current_admin: User,
    document_id: UUID,
) -> Document:
    if current_admin.role != ADMIN_ROLE:
        await _record_access_denied(
            current_user=current_admin,
            resource_type="document",
            resource_id=document_id,
            reason="role_mismatch",
        )
        raise _not_found("document")

    row = (
        await session.execute(
            select(Document, SupportAccessGrant)
            .join(KnowledgeBase, KnowledgeBase.id == Document.knowledge_base_id)
            .join(
                SupportAccessGrant,
                SupportAccessGrant.knowledge_base_id == KnowledgeBase.id,
            )
            .where(
                Document.id == document_id,
                Document.deleted_at.is_(None),
                *_active_grant_conditions(
                    knowledge_base_id=Document.knowledge_base_id,
                    admin_user_id=current_admin.id,
                ),
            )
            .order_by(SupportAccessGrant.created_at.desc(), SupportAccessGrant.id.desc())
            .limit(1)
            .with_for_update(of=SupportAccessGrant)
        )
    ).one_or_none()
    if row is None:
        await _record_access_denied(
            current_user=current_admin,
            resource_type="document",
            resource_id=document_id,
            reason="grant_unavailable",
        )
        raise _not_found("document")

    document, grant = row
    await _consume_locked_grant(
        session,
        current_admin=current_admin,
        grant=grant,
        resource_type="document",
        resource_id=document.id,
    )
    return document

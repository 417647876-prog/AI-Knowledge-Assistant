import asyncio
import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete, select, text, update

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.db.models import (
    ADMIN_ROLE,
    USER_ROLE,
    AuditEvent,
    Document,
    DocumentJob,
    KnowledgeBase,
    RefreshSession,
    SupportAccessGrant,
    User,
)
from app.db.session import session_factory
from app.main import create_app
from tests.database_cleanup import delete_owned_knowledge_bases

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="Set RUN_DATABASE_TESTS=1 to run PostgreSQL integration tests.",
    ),
]


@dataclass
class SupportContext:
    owner: User
    other_owner: User
    admin: User
    second_admin: User
    member: User
    owner_client: httpx.AsyncClient
    other_owner_client: httpx.AsyncClient
    admin_client: httpx.AsyncClient
    second_admin_client: httpx.AsyncClient
    member_client: httpx.AsyncClient
    knowledge_base_id: UUID
    other_knowledge_base_id: UUID
    document_id: UUID
    other_document_id: UUID


def _access_token(user: User) -> str:
    return create_access_token(user_id=user.id, role=user.role, settings=get_settings())


def _document(*, knowledge_base_id: UUID, uploader_id: UUID, suffix: str) -> Document:
    return Document(
        knowledge_base_id=knowledge_base_id,
        uploaded_by_user_id=uploader_id,
        original_file_name=f"support-private-{suffix}.txt",
        stored_file_name=f"{uuid4()}.txt",
        content_type="text/plain",
        file_extension=".txt",
        file_size=42,
        file_hash=uuid4().hex + uuid4().hex,
        status="ready",
    )


@pytest.fixture
async def support_context() -> AsyncIterator[SupportContext]:
    unique = uuid4().hex
    owner = User(
        id=uuid4(),
        username=f"support_owner_{unique}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    other_owner = User(
        id=uuid4(),
        username=f"support_other_{unique}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    admin = User(
        id=uuid4(),
        username=f"support_admin_{unique}",
        password_hash=hash_password("correct horse battery"),
        role=ADMIN_ROLE,
        is_active=True,
    )
    second_admin = User(
        id=uuid4(),
        username=f"support_admin_two_{unique}",
        password_hash=hash_password("correct horse battery"),
        role=ADMIN_ROLE,
        is_active=True,
    )
    member = User(
        id=uuid4(),
        username=f"support_member_{unique}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    users = [owner, other_owner, admin, second_admin, member]
    async with session_factory.begin() as session:
        session.add_all(users)
        await session.flush()
        knowledge_base = KnowledgeBase(name=f"support-private-kb-{unique}", owner_id=owner.id)
        other_knowledge_base = KnowledgeBase(
            name=f"support-other-kb-{unique}", owner_id=other_owner.id
        )
        session.add_all([knowledge_base, other_knowledge_base])
        await session.flush()
        document = _document(
            knowledge_base_id=knowledge_base.id,
            uploader_id=owner.id,
            suffix=unique,
        )
        other_document = _document(
            knowledge_base_id=other_knowledge_base.id,
            uploader_id=other_owner.id,
            suffix=f"other-{unique}",
        )
        session.add_all([document, other_document])
        await session.flush()
        session.add_all(
            [
                DocumentJob(
                    job_type="ingest_document",
                    resource_type="document",
                    resource_id=document.id,
                    owner_user_id=owner.id,
                    knowledge_base_id=knowledge_base.id,
                    status="succeeded",
                ),
                DocumentJob(
                    job_type="ingest_document",
                    resource_type="document",
                    resource_id=other_document.id,
                    owner_user_id=other_owner.id,
                    knowledge_base_id=other_knowledge_base.id,
                    status="succeeded",
                ),
            ]
        )

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    clients = [
        httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {_access_token(user)}"},
        )
        for user in users
    ]
    context = SupportContext(
        owner=owner,
        other_owner=other_owner,
        admin=admin,
        second_admin=second_admin,
        member=member,
        owner_client=clients[0],
        other_owner_client=clients[1],
        admin_client=clients[2],
        second_admin_client=clients[3],
        member_client=clients[4],
        knowledge_base_id=knowledge_base.id,
        other_knowledge_base_id=other_knowledge_base.id,
        document_id=document.id,
        other_document_id=other_document.id,
    )
    try:
        yield context
    finally:
        for client in clients:
            await client.aclose()
        user_ids = [user.id for user in users]
        async with session_factory.begin() as session:
            await session.execute(delete(AuditEvent).where(AuditEvent.actor_user_id.in_(user_ids)))
            await delete_owned_knowledge_bases(session, user_ids)
            await session.execute(
                delete(RefreshSession).where(RefreshSession.user_id.in_(user_ids))
            )
            await session.execute(delete(User).where(User.id.in_(user_ids)))


async def _create_grant(
    context: SupportContext,
    *,
    admin_id: UUID | None = None,
    request_id: str = "support-create",
    expires_in_minutes: int | None = None,
) -> httpx.Response:
    payload: dict[str, object] = {"admin_user_id": str(admin_id or context.admin.id)}
    if expires_in_minutes is not None:
        payload["expires_in_minutes"] = expires_in_minutes
    return await context.owner_client.post(
        f"/api/v1/knowledge-bases/{context.knowledge_base_id}/support-grants",
        json=payload,
        headers={"X-Request-ID": request_id},
    )


async def _events(*request_ids: str) -> list[AuditEvent]:
    async with session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(AuditEvent)
                    .where(AuditEvent.request_id.in_(request_ids))
                    .order_by(AuditEvent.created_at, AuditEvent.id)
                )
            ).all()
        )


@pytest.mark.asyncio
async def test_owner_creates_lists_and_revokes_grant_with_transactional_audit(
    support_context: SupportContext,
) -> None:
    created = await _create_grant(support_context, request_id="support-create-success")

    assert created.status_code == 201
    body = created.json()
    assert body["knowledge_base_id"] == str(support_context.knowledge_base_id)
    assert body["admin_user_id"] == str(support_context.admin.id)
    assert body["access_level"] == "read_only"
    assert body["revoked_at"] is None
    created_at = datetime.fromisoformat(body["created_at"])
    expires_at = datetime.fromisoformat(body["expires_at"])
    assert 29 * 60 <= (expires_at - created_at).total_seconds() <= 30 * 60

    listed = await support_context.owner_client.get(
        f"/api/v1/knowledge-bases/{support_context.knowledge_base_id}/support-grants"
    )
    hidden = await support_context.other_owner_client.get(
        f"/api/v1/knowledge-bases/{support_context.knowledge_base_id}/support-grants",
        headers={"X-Request-ID": "support-list-denied"},
    )
    revoked = await support_context.owner_client.delete(
        f"/api/v1/support-grants/{body['id']}",
        headers={"X-Request-ID": "support-revoke-success"},
    )

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [body["id"]]
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "KNOWLEDGE_BASE_NOT_FOUND"
    assert revoked.status_code == 204

    async with session_factory() as session:
        stored = await session.get(SupportAccessGrant, UUID(body["id"]))
        assert stored is not None and stored.revoked_at is not None
    events = await _events(
        "support-create-success", "support-list-denied", "support-revoke-success"
    )
    assert [(event.action, event.result) for event in events] == [
        ("support_grant_created", "success"),
        ("support_grant_list_denied", "denied"),
        ("support_grant_revoked", "success"),
    ]


@pytest.mark.asyncio
async def test_support_read_is_scoped_read_only_and_audited_without_sensitive_content(
    support_context: SupportContext,
) -> None:
    created = await _create_grant(support_context, request_id="support-read-create")
    assert created.status_code == 201

    knowledge_base = await support_context.admin_client.get(
        f"/api/v1/support/knowledge-bases/{support_context.knowledge_base_id}",
        headers={"X-Request-ID": "support-read-kb"},
    )
    documents = await support_context.admin_client.get(
        f"/api/v1/support/knowledge-bases/{support_context.knowledge_base_id}/documents",
        headers={"X-Request-ID": "support-read-list"},
    )
    document = await support_context.admin_client.get(
        f"/api/v1/support/documents/{support_context.document_id}",
        headers={"X-Request-ID": "support-read-document"},
    )
    other_knowledge_base = await support_context.admin_client.get(
        f"/api/v1/support/knowledge-bases/{support_context.other_knowledge_base_id}",
        headers={"X-Request-ID": "support-read-wrong-kb"},
    )
    other_document = await support_context.admin_client.get(
        f"/api/v1/support/documents/{support_context.other_document_id}",
        headers={"X-Request-ID": "support-read-wrong-document"},
    )

    assert knowledge_base.status_code == 200
    assert knowledge_base.json()["id"] == str(support_context.knowledge_base_id)
    assert documents.status_code == 200
    assert [item["id"] for item in documents.json()["items"]] == [str(support_context.document_id)]
    assert document.status_code == 200
    assert document.json()["id"] == str(support_context.document_id)
    assert other_knowledge_base.status_code == 404
    assert other_knowledge_base.json()["error"]["code"] == "KNOWLEDGE_BASE_NOT_FOUND"
    assert other_document.status_code == 404
    assert other_document.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"

    events = await _events(
        "support-read-kb",
        "support-read-list",
        "support-read-document",
        "support-read-wrong-kb",
        "support-read-wrong-document",
    )
    assert [(event.action, event.result) for event in events] == [
        ("support_access_used", "success"),
        ("support_access_used", "success"),
        ("support_access_used", "success"),
        ("support_access_denied", "denied"),
        ("support_access_denied", "denied"),
    ]
    serialized_summaries = json.dumps(
        [event.security_summary for event in events], ensure_ascii=False
    )
    assert "support-private" not in serialized_summaries
    assert "Bearer" not in serialized_summaries
    assert all(set(event.security_summary) <= {"access_level", "reason"} for event in events)

    async with session_factory() as session:
        stored = await session.get(SupportAccessGrant, UUID(created.json()["id"]))
        assert stored is not None and stored.last_used_at is not None


@pytest.mark.asyncio
async def test_grant_never_authorizes_ordinary_read_write_or_rag_routes(
    support_context: SupportContext,
) -> None:
    created = await _create_grant(support_context)
    assert created.status_code == 201

    ordinary_document = await support_context.admin_client.get(
        f"/api/v1/documents/{support_context.document_id}"
    )
    ordinary_list = await support_context.admin_client.get(
        f"/api/v1/knowledge-bases/{support_context.knowledge_base_id}/documents"
    )
    upload = await support_context.admin_client.post(
        f"/api/v1/knowledge-bases/{support_context.knowledge_base_id}/documents",
        files={"file": ("blocked.txt", "private body", "text/plain")},
    )
    reprocess = await support_context.admin_client.post(
        f"/api/v1/documents/{support_context.document_id}/reprocess"
    )
    question = await support_context.admin_client.post(
        f"/api/v1/knowledge-bases/{support_context.knowledge_base_id}/questions",
        json={"question": "Do not let support grants reach RAG."},
    )
    stream_question = await support_context.admin_client.post(
        f"/api/v1/knowledge-bases/{support_context.knowledge_base_id}/questions/stream",
        json={"question": "Do not let support grants reach streaming chat."},
    )
    deleted = await support_context.admin_client.delete(
        f"/api/v1/documents/{support_context.document_id}"
    )

    assert ordinary_document.status_code == 404
    assert ordinary_list.status_code == 404
    assert upload.status_code == 404
    assert reprocess.status_code == 404
    assert question.status_code == 404
    assert stream_question.status_code == 404
    assert stream_question.headers["content-type"].startswith("application/json")
    assert deleted.status_code == 404


@pytest.mark.asyncio
async def test_expired_revoked_and_downgraded_admin_access_is_denied_dynamically(
    support_context: SupportContext,
) -> None:
    expired = await _create_grant(support_context, request_id="support-expiring-create")
    assert expired.status_code == 201
    async with session_factory.begin() as session:
        await session.execute(
            text(
                "UPDATE support_access_grants "
                "SET expires_at = GREATEST(created_at + interval '1 microsecond', "
                "clock_timestamp() - interval '1 microsecond') WHERE id=:grant_id"
            ),
            {"grant_id": UUID(expired.json()["id"])},
        )

    expired_access = await support_context.admin_client.get(
        f"/api/v1/support/knowledge-bases/{support_context.knowledge_base_id}",
        headers={"X-Request-ID": "support-expired-denied"},
    )
    assert expired_access.status_code == 404

    replacement = await _create_grant(support_context, request_id="support-replacement-create")
    assert replacement.status_code == 201
    revoked = await support_context.owner_client.delete(
        f"/api/v1/support-grants/{replacement.json()['id']}",
        headers={"X-Request-ID": "support-revoked"},
    )
    assert revoked.status_code == 204
    revoked_access = await support_context.admin_client.get(
        f"/api/v1/support/knowledge-bases/{support_context.knowledge_base_id}",
        headers={"X-Request-ID": "support-revoked-denied"},
    )
    assert revoked_access.status_code == 404

    async with session_factory.begin() as session:
        await session.execute(
            update(User).where(User.id == support_context.admin.id).values(role=USER_ROLE)
        )
    downgraded_access = await support_context.admin_client.get(
        f"/api/v1/support/knowledge-bases/{support_context.knowledge_base_id}",
        headers={"X-Request-ID": "support-role-denied"},
    )
    assert downgraded_access.status_code == 404
    assert downgraded_access.json()["error"]["code"] == "KNOWLEDGE_BASE_NOT_FOUND"

    events = await _events(
        "support-expired-denied", "support-revoked-denied", "support-role-denied"
    )
    assert [(event.action, event.result) for event in events] == [
        ("support_access_denied", "denied"),
        ("support_access_denied", "denied"),
        ("support_access_denied", "denied"),
    ]


@pytest.mark.asyncio
async def test_wrong_owner_non_admin_target_and_client_identity_fields_are_rejected(
    support_context: SupportContext,
) -> None:
    wrong_owner = await support_context.other_owner_client.post(
        f"/api/v1/knowledge-bases/{support_context.knowledge_base_id}/support-grants",
        json={"admin_user_id": str(support_context.admin.id)},
        headers={"X-Request-ID": "support-create-wrong-owner"},
    )
    non_admin = await _create_grant(
        support_context,
        admin_id=support_context.member.id,
        request_id="support-create-non-admin",
    )
    spoofed = await support_context.owner_client.post(
        f"/api/v1/knowledge-bases/{support_context.knowledge_base_id}/support-grants",
        json={
            "admin_user_id": str(support_context.admin.id),
            "owner_user_id": str(support_context.other_owner.id),
            "access_level": "write",
            "admin_role": ADMIN_ROLE,
        },
    )

    assert wrong_owner.status_code == 404
    assert wrong_owner.json()["error"]["code"] == "KNOWLEDGE_BASE_NOT_FOUND"
    assert non_admin.status_code == 404
    assert non_admin.json()["error"]["code"] == "SUPPORT_ADMIN_NOT_FOUND"
    assert spoofed.status_code == 422

    events = await _events("support-create-wrong-owner", "support-create-non-admin")
    assert [(event.action, event.result) for event in events] == [
        ("support_grant_create_denied", "denied"),
        ("support_grant_create_denied", "denied"),
    ]


@pytest.mark.asyncio
async def test_concurrent_overlapping_grants_return_stable_conflict_and_keep_denial_audit(
    support_context: SupportContext,
) -> None:
    async def create(request_id: str) -> httpx.Response:
        return await _create_grant(
            support_context,
            admin_id=support_context.second_admin.id,
            request_id=request_id,
            expires_in_minutes=5,
        )

    first, second = await asyncio.gather(create("support-race-1"), create("support-race-2"))

    assert sorted([first.status_code, second.status_code]) == [201, 409]
    conflict = first if first.status_code == 409 else second
    assert conflict.json()["error"]["code"] == "SUPPORT_GRANT_CONFLICT"
    events = await _events("support-race-1", "support-race-2")
    assert sorted((event.action, event.result) for event in events) == [
        ("support_grant_create_denied", "conflict"),
        ("support_grant_created", "success"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("minutes", [4, 121])
async def test_grant_duration_is_bounded(support_context: SupportContext, minutes: int) -> None:
    response = await _create_grant(support_context, expires_in_minutes=minutes)

    assert response.status_code == 422

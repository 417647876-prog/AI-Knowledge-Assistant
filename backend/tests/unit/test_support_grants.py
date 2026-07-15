from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from app.api.v1 import support_grants


class _DatabaseError(Exception):
    def __init__(self, *, sqlstate: str, constraint_name: str | None = None) -> None:
        self.sqlstate = sqlstate
        self.diag = SimpleNamespace(constraint_name=constraint_name)


class _FailingSession:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def scalar(self, statement):
        return SimpleNamespace(id=uuid4())

    @asynccontextmanager
    async def begin_nested(self):
        yield

    def add(self, value) -> None:
        return None

    async def flush(self) -> None:
        raise self.error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        OperationalError(
            "INSERT",
            {},
            _DatabaseError(sqlstate="40P01"),
        ),
        IntegrityError(
            "INSERT",
            {},
            _DatabaseError(sqlstate="23P01", constraint_name="unrelated_constraint"),
        ),
    ],
    ids=["deadlock", "unrelated-exclusion"],
)
async def test_create_grant_does_not_disguise_unrelated_database_errors_as_conflict(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    knowledge_base_id = uuid4()
    owner = SimpleNamespace(id=uuid4())

    async def owned_knowledge_base(*args, **kwargs):
        return SimpleNamespace(id=knowledge_base_id)

    async def database_now(session):
        return datetime.now(UTC)

    async def unexpected_denial_audit(**kwargs):
        raise AssertionError("unrelated database errors must not be audited as grant conflicts")

    monkeypatch.setattr(support_grants, "get_owned_knowledge_base", owned_knowledge_base)
    monkeypatch.setattr(support_grants, "get_database_now", database_now)
    monkeypatch.setattr(support_grants, "_audit_management_denial", unexpected_denial_audit)

    with pytest.raises(type(error)) as raised:
        await support_grants.create_support_grant(
            knowledge_base_id=knowledge_base_id,
            payload=support_grants.SupportGrantCreate(admin_user_id=uuid4()),
            session=_FailingSession(error),
            current_user=owner,
        )

    assert raised.value is error

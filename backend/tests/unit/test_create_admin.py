from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import ADMIN_ROLE, User
from scripts.create_admin import create_admin_in_session, read_initial_password


def test_environment_password_takes_priority_without_prompting() -> None:
    def fail_if_called(_prompt: str) -> str:
        raise AssertionError("设置环境密码后不应提示输入")

    password = read_initial_password(
        environ={"INITIAL_ADMIN_PASSWORD": "environment secret 123"},
        password_prompt=fail_if_called,
    )

    assert password == "environment secret 123"


def test_interactive_password_requires_two_matching_entries() -> None:
    answers = iter(["interactive secret 123", "different secret 456"])

    with pytest.raises(RuntimeError, match="两次输入的密码不一致"):
        read_initial_password(environ={}, password_prompt=lambda _prompt: next(answers))


class ExistingUserSession:
    def __init__(self, existing: User | None) -> None:
        self.existing = existing
        self.added: User | None = None

    async def scalar(self, _statement: object) -> User | None:
        return self.existing

    def add(self, user: User) -> None:
        self.added = user

    async def flush(self) -> None:
        return None


class ConcurrentDuplicateSession(ExistingUserSession):
    async def flush(self) -> None:
        raise IntegrityError(
            "INSERT INTO users",
            {"password_hash": "sensitive-password-hash"},
            RuntimeError("duplicate username"),
        )


@pytest.mark.asyncio
async def test_create_admin_rejects_existing_normalized_username() -> None:
    existing = User(
        id=uuid4(),
        username="admin",
        password_hash="stored hash",
        role=ADMIN_ROLE,
    )

    with pytest.raises(RuntimeError, match="用户名已存在"):
        await create_admin_in_session(
            ExistingUserSession(existing),
            username=" ADMIN ",
            password="temporary pass 123",
        )


@pytest.mark.asyncio
async def test_create_admin_hides_hash_when_unique_constraint_detects_race() -> None:
    with pytest.raises(RuntimeError, match="用户名已存在") as caught:
        await create_admin_in_session(
            ConcurrentDuplicateSession(None),
            username="admin",
            password="temporary pass 123",
        )

    assert "sensitive-password-hash" not in str(caught.value)


@pytest.mark.asyncio
async def test_create_admin_normalizes_username_and_does_not_expose_secret(
    capsys: pytest.CaptureFixture[str],
) -> None:
    session = ExistingUserSession(None)

    user = await create_admin_in_session(
        session,
        username=" First.Admin ",
        password="temporary pass 123",
    )
    print(f"管理员已创建：id={user.id}, username={user.username}")

    output = capsys.readouterr().out
    assert session.added is user
    assert user.username == "first.admin"
    assert user.role == ADMIN_ROLE
    assert "temporary pass 123" not in output
    assert user.password_hash not in output
    assert str(user.id) in output
    assert user.username in output

import argparse
import asyncio
import getpass
import os
import re
from collections.abc import Callable, Mapping
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.event_loop import new_event_loop
from app.core.security import hash_password
from app.db.models import ADMIN_ROLE, User
from app.db.session import session_factory

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def read_initial_password(
    *,
    environ: Mapping[str, str] = os.environ,
    password_prompt: Callable[[str], str] = getpass.getpass,
) -> str:
    password = environ.get("INITIAL_ADMIN_PASSWORD")
    if password is None:
        password = password_prompt("管理员密码：")
        confirmation = password_prompt("再次输入管理员密码：")
        if password != confirmation:
            raise RuntimeError("两次输入的密码不一致。")
    if not 12 <= len(password) <= 128:
        raise RuntimeError("密码长度必须在 12 到 128 个字符之间。")
    return password


async def create_admin_in_session(
    session: AsyncSession,
    *,
    username: str,
    password: str,
) -> User:
    normalized_username = username.strip().lower()
    if not 3 <= len(normalized_username) <= 50 or not USERNAME_PATTERN.fullmatch(
        normalized_username
    ):
        raise RuntimeError("用户名格式无效。")
    existing = await session.scalar(select(User.id).where(User.username == normalized_username))
    if existing is not None:
        raise RuntimeError("用户名已存在，拒绝覆盖。")

    user = User(
        id=uuid4(),
        username=normalized_username,
        password_hash=hash_password(password),
        role=ADMIN_ROLE,
        is_active=True,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError:
        raise RuntimeError("用户名已存在，拒绝覆盖。") from None
    return user


async def create_initial_admin(*, username: str, password: str) -> User:
    async with session_factory() as session:
        async with session.begin():
            return await create_admin_in_session(
                session,
                username=username,
                password=password,
            )


def run_create_initial_admin(*, username: str, password: str) -> User:
    """使用 psycopg 在 Windows 支持的 SelectorEventLoop 执行命令。"""
    with asyncio.Runner(loop_factory=new_event_loop) as runner:
        return runner.run(create_initial_admin(username=username, password=password))


def main() -> None:
    parser = argparse.ArgumentParser(description="创建首个管理员用户")
    parser.add_argument("--username", required=True, help="管理员用户名")
    args = parser.parse_args()
    password = read_initial_password()
    user = run_create_initial_admin(username=args.username, password=password)
    print(f"管理员已创建：id={user.id}, username={user.username}")


if __name__ == "__main__":
    main()

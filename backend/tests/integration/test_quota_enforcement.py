import asyncio
import os
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.core.security import hash_password
from app.db.models import USER_ROLE, User, UserQuota
from app.db.session import session_factory
from app.quotas.service import QuotaDefaults, consume_question

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="设置 RUN_DATABASE_TESTS=1 后运行 PostgreSQL 集成测试",
    ),
]


@pytest.mark.asyncio
async def test_daily_question_quota_resets_in_shanghai_day_and_is_atomic() -> None:
    user = User(
        id=uuid4(),
        username=f"quota_{uuid4().hex}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    defaults = QuotaDefaults(daily_questions=1, daily_uploads=20, storage_bytes=500 * 1024**2)
    first_day = date(2026, 7, 17)
    try:
        async with session_factory.begin() as session:
            session.add(user)
        async with session_factory.begin() as session:
            await consume_question(session, user_id=user.id, defaults=defaults, today=first_day)
        async with session_factory.begin() as session:
            await consume_question(
                session, user_id=user.id, defaults=defaults, today=date(2026, 7, 18)
            )

        async def consume_once() -> bool:
            try:
                async with session_factory.begin() as session:
                    await consume_question(
                        session, user_id=user.id, defaults=defaults, today=date(2026, 7, 19)
                    )
                return True
            except Exception:
                return False

        results = await asyncio.gather(consume_once(), consume_once())
        assert results.count(True) == 1
    finally:
        async with session_factory.begin() as session:
            await session.execute(delete(UserQuota).where(UserQuota.user_id == user.id))
            await session.execute(delete(User).where(User.id == user.id))

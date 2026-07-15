import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.db.models.document_job import DocumentJob
from app.jobs.repository import (
    LeaseLostError,
    cancel_jobs_for_resource,
    claim_next_job,
    complete_job,
    enqueue_job,
    fail_job,
    renew_lease,
    update_job_stage,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="设置 RUN_DATABASE_TESTS=1 后运行 PostgreSQL 集成测试",
    ),
]


@pytest.fixture
def temporary_database_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    configured_url = make_url(Settings().database_url)
    database_name = f"knowledge_job_repository_test_{uuid4().hex}"
    admin_engine = create_engine(
        configured_url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    database_url = configured_url.set(database=database_name).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config("alembic.ini")
    config.set_main_option("path_separator", "os")
    command.upgrade(config, "head")
    try:
        yield database_url
    finally:
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=:database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin_engine.dispose()
        get_settings.cache_clear()


@pytest.fixture
async def session_factory(
    temporary_database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(temporary_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
async def job_ids(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, UUID]:
    ids = {"user_id": uuid4(), "knowledge_base_id": uuid4(), "resource_id": uuid4()}
    async with session_factory.begin() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, username, password_hash, role, is_active) "
                "VALUES (:user_id, :username, 'hash', 'user', true)"
            ),
            {**ids, "username": f"worker_{uuid4().hex}"},
        )
        await session.execute(
            text(
                "INSERT INTO knowledge_bases (id, name, owner_id) "
                "VALUES (:knowledge_base_id, 'Worker 测试', :user_id)"
            ),
            ids,
        )
    return ids


async def _enqueue(
    factory: async_sessionmaker[AsyncSession], ids: dict[str, UUID], *, max_attempts: int = 3
) -> UUID:
    async with factory.begin() as session:
        job = await enqueue_job(
            session,
            job_type="ingest_document",
            resource_type="document",
            resource_id=ids["resource_id"],
            owner_user_id=ids["user_id"],
            knowledge_base_id=ids["knowledge_base_id"],
            max_attempts=max_attempts,
        )
        await session.flush()
        return job.id


@pytest.mark.asyncio
async def test_two_workers_compete_then_expired_lease_is_reclaimed_and_old_token_loses(
    session_factory: async_sessionmaker[AsyncSession], job_ids: dict[str, UUID]
) -> None:
    job_id = await _enqueue(session_factory, job_ids)
    now = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)

    async with session_factory() as first_session, session_factory() as second_session:
        first, second = await asyncio.gather(
            claim_next_job(first_session, worker_id="worker-a", now=now, lease_seconds=120),
            claim_next_job(second_session, worker_id="worker-b", now=now, lease_seconds=120),
        )
        assert sum(lease is not None for lease in (first, second)) == 1
        winner = first or second
        assert winner is not None
        await first_session.commit()
        await second_session.commit()

    expired_at = now + timedelta(seconds=121)
    async with session_factory.begin() as session:
        replacement = await claim_next_job(
            session, worker_id="worker-c", now=expired_at, lease_seconds=120
        )
    assert replacement is not None
    assert replacement.job_id == job_id
    assert replacement.lease_token != winner.lease_token

    async with session_factory.begin() as session:
        completed = await complete_job(
            session,
            job_id=job_id,
            lease_token=winner.lease_token,
            chunk_count=3,
            now=expired_at,
        )
    assert completed is False


@pytest.mark.asyncio
async def test_expired_token_cannot_be_renewed_or_completed_before_reclaim(
    session_factory: async_sessionmaker[AsyncSession], job_ids: dict[str, UUID]
) -> None:
    job_id = await _enqueue(session_factory, job_ids)
    now = datetime(2026, 7, 16, 10, 30, tzinfo=UTC)
    async with session_factory.begin() as session:
        lease = await claim_next_job(session, worker_id="worker-a", now=now, lease_seconds=120)
    assert lease is not None

    after_expiry = now + timedelta(seconds=121)
    async with session_factory.begin() as session:
        assert not await renew_lease(
            session,
            job_id=job_id,
            lease_token=lease.lease_token,
            now=after_expiry,
            lease_seconds=120,
        )
        assert not await update_job_stage(
            session,
            job_id=job_id,
            lease_token=lease.lease_token,
            stage="store",
            now=after_expiry,
        )
        assert not await complete_job(
            session,
            job_id=job_id,
            lease_token=lease.lease_token,
            chunk_count=3,
            now=after_expiry,
        )


@pytest.mark.asyncio
async def test_retryable_failures_wait_30_then_120_seconds_and_third_is_terminal(
    session_factory: async_sessionmaker[AsyncSession], job_ids: dict[str, UUID]
) -> None:
    job_id = await _enqueue(session_factory, job_ids)
    first_now = datetime(2026, 7, 16, 11, 0, tzinfo=UTC)

    for attempt, expected_status, delay in (
        (1, "retry_wait", 30),
        (2, "retry_wait", 120),
        (3, "failed", 0),
    ):
        claim_at = (
            first_now
            if attempt == 1
            else first_now + timedelta(seconds=150 if attempt == 3 else 30)
        )
        async with session_factory.begin() as session:
            lease = await claim_next_job(
                session,
                worker_id=f"worker-{attempt}",
                now=claim_at,
                lease_seconds=120,
            )
            assert lease is not None
            status = await fail_job(
                session,
                job_id=job_id,
                lease_token=lease.lease_token,
                code="TEMPORARY_NETWORK_ERROR",
                message="任务处理暂时失败，请稍后重试。",
                retryable=True,
                now=claim_at,
            )
        assert status == expected_status
        async with session_factory() as session:
            job = await session.get(DocumentJob, job_id)
            assert job is not None
            assert job.run_after == claim_at + timedelta(seconds=delay)


@pytest.mark.asyncio
async def test_mutations_require_current_lease_and_resource_jobs_can_be_canceled(
    session_factory: async_sessionmaker[AsyncSession], job_ids: dict[str, UUID]
) -> None:
    job_id = await _enqueue(session_factory, job_ids)
    now = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    async with session_factory.begin() as session:
        lease = await claim_next_job(session, worker_id="worker-a", now=now, lease_seconds=120)
        assert lease is not None
        assert await renew_lease(
            session,
            job_id=job_id,
            lease_token=lease.lease_token,
            now=now + timedelta(seconds=10),
            lease_seconds=120,
        )
        assert await update_job_stage(
            session,
            job_id=job_id,
            lease_token=lease.lease_token,
            stage="embed",
            now=now + timedelta(seconds=10),
        )
        assert not await update_job_stage(
            session,
            job_id=job_id,
            lease_token=uuid4(),
            stage="store",
            now=now + timedelta(seconds=10),
        )
        with pytest.raises(LeaseLostError, match="租约"):
            await fail_job(
                session,
                job_id=job_id,
                lease_token=uuid4(),
                code="MODEL_TIMEOUT",
                message="任务处理暂时失败，请稍后重试。",
                retryable=True,
                now=now + timedelta(seconds=10),
            )
        assert await complete_job(
            session,
            job_id=job_id,
            lease_token=lease.lease_token,
            chunk_count=4,
            now=now + timedelta(seconds=20),
        )

    job_ids["resource_id"] = uuid4()
    await _enqueue(session_factory, job_ids)
    async with session_factory.begin() as session:
        canceled = await cancel_jobs_for_resource(
            session,
            resource_type="document",
            resource_id=job_ids["resource_id"],
            now=now,
        )
    assert canceled == 1

    async with session_factory() as session:
        jobs = (await session.scalars(select(DocumentJob))).all()
    statuses_by_resource = {job.resource_id: job.status for job in jobs}
    assert statuses_by_resource == {
        lease.resource_id: "succeeded",
        job_ids["resource_id"]: "canceled",
    }

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import Settings, get_settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="设置 RUN_DATABASE_TESTS=1 后运行 PostgreSQL 集成测试",
    ),
]


@pytest.fixture
def temporary_database_url() -> Iterator[str]:
    configured_url = make_url(Settings().database_url)
    database_name = f"knowledge_document_job_test_{uuid4().hex}"
    admin_engine = create_engine(
        configured_url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    try:
        yield configured_url.set(database=database_name).render_as_string(hide_password=False)
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


def _alembic_config(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config("alembic.ini")
    config.set_main_option("path_separator", "os")
    return config


def _insert_legacy_job(engine, *, status: str = "running") -> dict[str, object]:
    ids = {
        "user_id": uuid4(),
        "knowledge_base_id": uuid4(),
        "document_id": uuid4(),
        "job_id": uuid4(),
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, username, password_hash, role, is_active) "
                "VALUES (:user_id, 'job_migration_user', 'hash', 'user', true)"
            ),
            ids,
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_bases (id, name, owner_id) "
                "VALUES (:knowledge_base_id, '任务迁移知识库', :user_id)"
            ),
            ids,
        )
        connection.execute(
            text(
                "INSERT INTO documents "
                "(id, knowledge_base_id, original_file_name, stored_file_name, content_type, "
                "file_extension, file_size, file_hash, status) VALUES "
                "(:document_id, :knowledge_base_id, '旧任务.txt', 'stored.txt', "
                "'text/plain', '.txt', 8, :file_hash, 'processing')"
            ),
            {**ids, "file_hash": uuid4().hex * 2},
        )
        connection.execute(
            text(
                "INSERT INTO ingestion_jobs "
                "(id, document_id, status, stage, chunk_count, created_at, started_at) "
                "VALUES (:job_id, :document_id, :status, 'chunk', 2, :created_at, :started_at)"
            ),
            {
                **ids,
                "status": status,
                "created_at": datetime(2026, 7, 16, 8, 0, tzinfo=UTC),
                "started_at": datetime(2026, 7, 16, 8, 1, tzinfo=UTC),
            },
        )
    return ids


def test_upgrade_converts_legacy_ingestion_job_in_place_and_supports_downgrade(
    temporary_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _alembic_config(temporary_database_url, monkeypatch)
    engine = create_engine(temporary_database_url)
    try:
        command.upgrade(config, "20260715_05")
        ids = _insert_legacy_job(engine)

        command.upgrade(config, "20260716_06")

        with engine.connect() as connection:
            migrated = connection.execute(
                text(
                    "SELECT id, job_type, resource_type, resource_id, owner_user_id, "
                    "knowledge_base_id, status, attempt_count, max_attempts, stage, chunk_count "
                    "FROM document_jobs WHERE id=:job_id"
                ),
                ids,
            ).one()
            table_names = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                        "AND tablename IN ('ingestion_jobs','document_jobs','worker_heartbeats')"
                    )
                )
            }
            status_constraint = connection.scalar(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid='document_jobs'::regclass "
                    "AND conname='ck_document_jobs_status_values'"
                )
            )
            active_index = connection.scalar(
                text(
                    "SELECT indexdef FROM pg_indexes WHERE schemaname='public' "
                    "AND indexname='uq_document_jobs_active_resource'"
                )
            )

        assert migrated == (
            ids["job_id"],
            "ingest_document",
            "document",
            ids["document_id"],
            ids["user_id"],
            ids["knowledge_base_id"],
            "processing",
            0,
            3,
            "chunk",
            2,
        )
        assert table_names == {"document_jobs", "worker_heartbeats"}
        assert status_constraint is not None
        assert all(value in status_constraint for value in ("pending", "processing", "canceled"))
        assert active_index is not None
        assert "UNIQUE" in active_index
        assert "WHERE" in active_index
        assert "retry_wait" in active_index

        command.downgrade(config, "20260715_05")
        with engine.connect() as connection:
            legacy = connection.execute(
                text("SELECT id, document_id, status FROM ingestion_jobs WHERE id=:job_id"), ids
            ).one()
        assert legacy == (ids["job_id"], ids["document_id"], "running")
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_downgrade_rejects_cleanup_jobs_without_losing_them(
    temporary_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _alembic_config(temporary_database_url, monkeypatch)
    engine = create_engine(temporary_database_url)
    try:
        command.upgrade(config, "20260715_05")
        ids = _insert_legacy_job(engine, status="succeeded")
        command.upgrade(config, "20260716_06")
        cleanup_job_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO document_jobs "
                    "(id, job_type, resource_type, resource_id, owner_user_id, knowledge_base_id, "
                    "status, run_after, attempt_count, max_attempts, chunk_count, created_at) "
                    "VALUES (:id, 'purge_document', 'document', :document_id, :user_id, "
                    ":knowledge_base_id, 'pending', now(), 0, 3, 0, now())"
                ),
                {"id": cleanup_job_id, **ids},
            )

        with pytest.raises(RuntimeError, match="不能无损降级"):
            command.downgrade(config, "20260715_05")

        with engine.connect() as connection:
            version = connection.scalar(text("SELECT version_num FROM alembic_version"))
            preserved_type = connection.scalar(
                text("SELECT job_type FROM document_jobs WHERE id=:id"), {"id": cleanup_job_id}
            )
        assert version == "20260716_06"
        assert preserved_type == "purge_document"
    finally:
        engine.dispose()
        get_settings.cache_clear()

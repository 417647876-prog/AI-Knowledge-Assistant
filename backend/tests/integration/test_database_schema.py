import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

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
    database_name = f"knowledge_migration_test_{uuid4().hex}"
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


def test_auth_migration_preserves_null_owner_data_on_failed_upgrade(
    temporary_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", temporary_database_url)
    get_settings.cache_clear()
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("path_separator", "os")
    knowledge_base_id = uuid4()
    engine = create_engine(temporary_database_url)
    try:
        command.upgrade(alembic_config, "20260713_02")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO knowledge_bases (id, name, owner_id) "
                    "VALUES (:id, '旧知识库', NULL)"
                ),
                {"id": knowledge_base_id},
            )

        with pytest.raises(RuntimeError, match="owner_id 为空"):
            command.upgrade(alembic_config, "20260713_03")

        with engine.connect() as connection:
            preserved_row = connection.execute(
                text("SELECT id, name FROM knowledge_bases WHERE id=:id"),
                {"id": knowledge_base_id},
            ).one()
            version = connection.scalar(text("SELECT version_num FROM alembic_version"))
            users_exists_after_failure = connection.scalar(
                text("SELECT to_regclass('public.users') IS NOT NULL")
            )
        assert preserved_row == (knowledge_base_id, "旧知识库")
        assert version == "20260713_02"
        assert users_exists_after_failure is False

        with engine.begin() as connection:
            connection.execute(text("DELETE FROM knowledge_bases"))
        command.upgrade(alembic_config, "20260713_03")
        command.downgrade(alembic_config, "20260713_02")
        command.upgrade(alembic_config, "20260713_03")

        with engine.connect() as connection:
            final_version = connection.scalar(text("SELECT version_num FROM alembic_version"))
            auth_columns = {
                (row.table_name, row.column_name): (
                    row.data_type,
                    row.character_maximum_length,
                )
                for row in connection.execute(
                    text(
                        "SELECT table_name, column_name, data_type, "
                        "character_maximum_length FROM information_schema.columns "
                        "WHERE table_schema='public' AND ("
                        "(table_name='users' AND column_name IN "
                        "('username','password_hash')) OR "
                        "(table_name='refresh_sessions' AND column_name IN "
                        "('token_hash','replaced_by_id','replaced_by_session_id')))"
                    )
                )
            }
            role_check = connection.scalar(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid='users'::regclass AND contype='c' "
                    "AND conname='ck_users_role_values'"
                )
            )
        assert final_version == "20260713_03"
        assert auth_columns == {
            ("users", "username"): ("character varying", 50),
            ("users", "password_hash"): ("text", None),
            ("refresh_sessions", "token_hash"): ("character", 64),
            ("refresh_sessions", "replaced_by_id"): ("uuid", None),
        }
        assert role_check is not None
        assert "admin" in role_check and "user" in role_check
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_ingestion_job_created_at_migration_backfills_existing_jobs(
    temporary_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", temporary_database_url)
    get_settings.cache_clear()
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("path_separator", "os")
    user_id = uuid4()
    knowledge_base_id = uuid4()
    document_id = uuid4()
    job_id = uuid4()
    started_at = datetime(2026, 7, 14, 8, 30, tzinfo=UTC)
    engine = create_engine(temporary_database_url)
    try:
        command.upgrade(alembic_config, "20260713_03")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(id, username, password_hash, role, is_active) "
                    "VALUES (:id, 'migration_user', 'hash', 'user', true)"
                ),
                {"id": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO knowledge_bases (id, name, owner_id) "
                    "VALUES (:id, '迁移测试知识库', :owner_id)"
                ),
                {"id": knowledge_base_id, "owner_id": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO documents "
                    "(id, knowledge_base_id, original_file_name, stored_file_name, "
                    "content_type, file_extension, file_size, file_hash, status) "
                    "VALUES (:id, :knowledge_base_id, '制度.txt', 'stored.txt', "
                    "'text/plain', '.txt', 6, :file_hash, 'ready')"
                ),
                {
                    "id": document_id,
                    "knowledge_base_id": knowledge_base_id,
                    "file_hash": uuid4().hex * 2,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO ingestion_jobs "
                    "(id, document_id, status, stage, chunk_count, started_at) "
                    "VALUES (:id, :document_id, 'succeeded', 'store', 1, :started_at)"
                ),
                {"id": job_id, "document_id": document_id, "started_at": started_at},
            )

        command.upgrade(alembic_config, "20260715_05")

        with engine.connect() as connection:
            created_at = connection.scalar(
                text("SELECT created_at FROM ingestion_jobs WHERE id=:id"),
                {"id": job_id},
            )
            is_nullable = connection.scalar(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='ingestion_jobs' "
                    "AND column_name='created_at'"
                )
            )
            index_columns = (
                connection.execute(
                    text(
                        "SELECT a.attname FROM pg_class i "
                        "JOIN pg_index ix ON ix.indexrelid=i.oid "
                        "JOIN pg_attribute a ON a.attrelid=ix.indrelid "
                        "AND a.attnum=ANY(ix.indkey) "
                        "WHERE i.relname='ix_ingestion_jobs_document_id_created_at' "
                        "ORDER BY array_position(ix.indkey, a.attnum)"
                    )
                )
                .scalars()
                .all()
            )

        assert created_at == started_at
        assert is_nullable == "NO"
        assert index_columns == ["document_id", "created_at"]
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_hybrid_search_migration_preserves_chunks_and_supports_downgrade(
    temporary_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", temporary_database_url)
    get_settings.cache_clear()
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("path_separator", "os")
    user_id = uuid4()
    knowledge_base_id = uuid4()
    document_id = uuid4()
    chunk_id = uuid4()
    engine = create_engine(temporary_database_url)
    try:
        command.upgrade(alembic_config, "20260714_04")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(id, username, password_hash, role, is_active) "
                    "VALUES (:id, 'hybrid_migration_user', 'hash', 'user', true)"
                ),
                {"id": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO knowledge_bases (id, name, owner_id) "
                    "VALUES (:id, '混合检索迁移测试', :owner_id)"
                ),
                {"id": knowledge_base_id, "owner_id": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO documents "
                    "(id, knowledge_base_id, original_file_name, stored_file_name, "
                    "content_type, file_extension, file_size, file_hash, status) "
                    "VALUES (:id, :knowledge_base_id, '制度.txt', 'stored.txt', "
                    "'text/plain', '.txt', 6, :file_hash, 'ready')"
                ),
                {
                    "id": document_id,
                    "knowledge_base_id": knowledge_base_id,
                    "file_hash": uuid4().hex * 2,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO document_chunks "
                    "(id, document_id, knowledge_base_id, chunk_index, content, "
                    "content_hash, metadata, embedding) "
                    "VALUES (:id, :document_id, :knowledge_base_id, 0, '旧片段', "
                    ":content_hash, '{}'::jsonb, "
                    "CAST(array_fill(0::real, ARRAY[512]) AS vector))"
                ),
                {
                    "id": chunk_id,
                    "document_id": document_id,
                    "knowledge_base_id": knowledge_base_id,
                    "content_hash": uuid4().hex * 2,
                },
            )

        command.upgrade(alembic_config, "head")

        with engine.connect() as connection:
            migrated_chunk = connection.execute(
                text("SELECT search_text, search_vector::text FROM document_chunks WHERE id=:id"),
                {"id": chunk_id},
            ).one()
            columns = {
                row.column_name: (row.is_nullable, row.column_default, row.is_generated)
                for row in connection.execute(
                    text(
                        "SELECT column_name, is_nullable, column_default, is_generated "
                        "FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='document_chunks' "
                        "AND column_name IN ('search_text', 'search_vector')"
                    )
                )
            }
            index_definition = connection.scalar(
                text(
                    "SELECT indexdef FROM pg_indexes WHERE schemaname='public' "
                    "AND indexname='ix_document_chunks_search_vector'"
                )
            )

        assert migrated_chunk == ("", "")
        assert columns["search_text"] == ("NO", "''::text", "NEVER")
        assert columns["search_vector"][0] == "YES"
        assert columns["search_vector"][2] == "ALWAYS"
        assert index_definition is not None
        assert "USING gin (search_vector)" in index_definition

        command.downgrade(alembic_config, "20260714_04")
        with engine.connect() as connection:
            remaining_search_columns = connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='document_chunks' "
                    "AND column_name IN ('search_text', 'search_vector')"
                )
            )
        assert remaining_search_columns == 0

        command.upgrade(alembic_config, "head")
        with engine.connect() as connection:
            restored_search_text = connection.scalar(
                text("SELECT search_text FROM document_chunks WHERE id=:id"),
                {"id": chunk_id},
            )
        assert restored_search_text == ""
    finally:
        engine.dispose()
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_hybrid_search_columns_and_index_exist() -> None:
    database_url = Settings().database_url
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            search_columns = {
                row.column_name: row.is_generated
                for row in await connection.execute(
                    text(
                        "SELECT column_name, is_generated FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='document_chunks' "
                        "AND column_name IN ('search_text', 'search_vector')"
                    )
                )
            }
            index_definition = await connection.scalar(
                text(
                    "SELECT indexdef FROM pg_indexes WHERE schemaname='public' "
                    "AND indexname='ix_document_chunks_search_vector'"
                )
            )
    finally:
        await engine.dispose()

    assert search_columns == {"search_text": "NEVER", "search_vector": "ALWAYS"}
    assert index_definition is not None
    assert "USING gin (search_vector)" in index_definition


@pytest.mark.asyncio
async def test_auth_migration_creates_users_sessions_and_owned_knowledge_bases() -> None:
    database_url = Settings().database_url
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            extension_exists = await connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector')")
            )
            table_rows = await connection.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname='public' AND tablename IN "
                    "('knowledge_bases','documents','document_chunks','document_jobs',"
                    "'worker_heartbeats','users','refresh_sessions','support_access_grants',"
                    "'audit_events','conversations','conversation_messages','llm_usage_events',"
                    "'answer_observations','answer_feedback','user_quotas',"
                    "'quality_evaluation_runs')"
                )
            )
            table_names = {row[0] for row in table_rows}
            owner_is_nullable = await connection.scalar(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='knowledge_bases' "
                    "AND column_name='owner_id'"
                )
            )
            owner_foreign_key_count = await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_constraint "
                    "WHERE conrelid='knowledge_bases'::regclass "
                    "AND contype='f' AND conname='fk_knowledge_bases_owner_id_users'"
                )
            )
            auth_column_rows = await connection.execute(
                text(
                    "SELECT table_name, column_name, data_type, "
                    "character_maximum_length FROM information_schema.columns "
                    "WHERE table_schema='public' AND ("
                    "(table_name='users' AND column_name IN "
                    "('username','password_hash')) OR "
                    "(table_name='refresh_sessions' AND column_name IN "
                    "('token_hash','replaced_by_id','replaced_by_session_id')))"
                )
            )
            auth_columns = {
                (row.table_name, row.column_name): (
                    row.data_type,
                    row.character_maximum_length,
                )
                for row in auth_column_rows
            }
            role_check = await connection.scalar(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid='users'::regclass AND contype='c' "
                    "AND conname='ck_users_role_values'"
                )
            )
    finally:
        await engine.dispose()

    assert extension_exists is True
    assert table_names == {
        "knowledge_bases",
        "documents",
        "document_chunks",
        "document_jobs",
        "worker_heartbeats",
        "users",
        "refresh_sessions",
        "support_access_grants",
        "audit_events",
        "conversations",
        "conversation_messages",
        "llm_usage_events",
        "answer_observations",
        "answer_feedback",
        "user_quotas",
        "quality_evaluation_runs",
    }
    assert owner_is_nullable == "NO"
    assert owner_foreign_key_count == 1
    assert auth_columns == {
        ("users", "username"): ("character varying", 50),
        ("users", "password_hash"): ("text", None),
        ("refresh_sessions", "token_hash"): ("character", 64),
        ("refresh_sessions", "replaced_by_id"): ("uuid", None),
    }
    assert role_check is not None
    assert "admin" in role_check and "user" in role_check

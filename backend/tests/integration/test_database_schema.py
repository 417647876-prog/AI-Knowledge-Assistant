import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="设置 RUN_DATABASE_TESTS=1 后运行 PostgreSQL 集成测试",
    ),
]


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
                    "('knowledge_bases','documents','document_chunks','ingestion_jobs',"
                    "'users','refresh_sessions')"
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
    finally:
        await engine.dispose()

    assert extension_exists is True
    assert table_names == {
        "knowledge_bases",
        "documents",
        "document_chunks",
        "ingestion_jobs",
        "users",
        "refresh_sessions",
    }
    assert owner_is_nullable == "NO"
    assert owner_foreign_key_count == 1

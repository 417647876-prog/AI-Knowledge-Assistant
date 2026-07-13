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
async def test_initial_migration_creates_pgvector_and_core_tables() -> None:
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
                    "('knowledge_bases','documents','document_chunks','ingestion_jobs')"
                )
            )
            table_names = {row[0] for row in table_rows}
    finally:
        await engine.dispose()

    assert extension_exists is True
    assert table_names == {
        "knowledge_bases",
        "documents",
        "document_chunks",
        "ingestion_jobs",
    }

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import database_is_ready
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.db.session import get_session

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str


class ReadyResponse(BaseModel):
    status: Literal["ready"]


@lru_cache
def expected_migration_revision() -> str:
    project_directory = Path(__file__).resolve().parents[3]
    config = Config(str(project_directory / "alembic.ini"))
    config.set_main_option("script_location", str(project_directory / "migrations"))
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise RuntimeError("Alembic migration head is not configured")
    return head


async def migrations_are_current(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> bool:
    """仅在数据库已可访问时确认其模式已迁移到当前应用版本。"""
    try:
        result = await session.execute(text("SELECT version_num FROM alembic_version"))
    except SQLAlchemyError:
        return False
    return result.scalar_one_or_none() == expected_migration_revision()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service=get_settings().app_name)


@router.get("/ready", response_model=ReadyResponse)
async def ready(
    is_ready: Annotated[bool, Depends(database_is_ready)],
    migrations_current: Annotated[bool, Depends(migrations_are_current)],
) -> ReadyResponse:
    if not is_ready:
        raise AppError(
            code="DATABASE_UNAVAILABLE",
            message="数据库暂不可用。",
            status_code=503,
        )
    if not migrations_current:
        raise AppError(
            code="MIGRATIONS_OUTDATED",
            message="数据库迁移版本尚未就绪。",
            status_code=503,
        )
    return ReadyResponse(status="ready")

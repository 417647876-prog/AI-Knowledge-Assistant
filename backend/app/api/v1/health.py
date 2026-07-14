from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.dependencies import database_is_ready
from app.core.config import get_settings
from app.core.exceptions import AppError

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str


class ReadyResponse(BaseModel):
    status: Literal["ready"]


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service=get_settings().app_name)


@router.get("/ready", response_model=ReadyResponse)
async def ready(
    is_ready: Annotated[bool, Depends(database_is_ready)],
) -> ReadyResponse:
    if not is_ready:
        raise AppError(
            code="DATABASE_UNAVAILABLE",
            message="数据库暂不可用。",
            status_code=503,
        )
    return ReadyResponse(status="ready")

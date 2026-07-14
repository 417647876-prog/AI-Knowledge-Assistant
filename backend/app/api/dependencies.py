from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.health import check_database
from app.db.session import get_session


async def database_is_ready(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> bool:
    return await check_database(session)

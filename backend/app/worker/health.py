import argparse
import asyncio
from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.session import session_factory
from app.jobs.repository import worker_heartbeat_is_fresh


async def check_health(max_age_seconds: int) -> bool:
    settings = get_settings()
    try:
        async with session_factory() as session:
            return await worker_heartbeat_is_fresh(
                session,
                worker_id=settings.worker_id,
                now=datetime.now(UTC),
                max_age_seconds=max_age_seconds,
            )
    except SQLAlchemyError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="检查数据库任务 Worker 心跳")
    parser.add_argument("--max-age-seconds", type=int, default=60)
    args = parser.parse_args()
    if args.max_age_seconds <= 0:
        parser.error("--max-age-seconds 必须大于 0")
    return 0 if asyncio.run(check_health(args.max_age_seconds)) else 1


if __name__ == "__main__":
    raise SystemExit(main())

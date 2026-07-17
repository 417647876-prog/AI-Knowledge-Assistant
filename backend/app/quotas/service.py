from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.db.models import Document, LlmUsageEvent, User, UserQuota

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_GLOBAL_COST_LOCK_KEY = 118_637_401


@dataclass(frozen=True)
class QuotaDefaults:
    daily_questions: int
    daily_uploads: int
    storage_bytes: int


@dataclass(frozen=True)
class QuotaSnapshot:
    daily_question_limit: int
    daily_upload_limit: int
    storage_bytes_limit: int
    question_count: int
    upload_count: int
    storage_bytes_used: int

    @property
    def question_remaining(self) -> int:
        return max(0, self.daily_question_limit - self.question_count)

    @property
    def upload_remaining(self) -> int:
        return max(0, self.daily_upload_limit - self.upload_count)

    @property
    def storage_bytes_remaining(self) -> int:
        return max(0, self.storage_bytes_limit - self.storage_bytes_used)


def shanghai_date(moment: datetime | None = None) -> date:
    source = moment or datetime.now(UTC)
    if source.tzinfo is None or source.utcoffset() is None:
        raise ValueError("moment 必须包含时区")
    return source.astimezone(_SHANGHAI).date()


def effective_limit(override: int | None, default: int) -> int:
    return default if override is None else override


def validate_cost(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError("费用必须是非负有限 Decimal")
    return value


async def _locked_active_user(session: AsyncSession, user_id) -> User:
    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None or not user.is_active:
        raise AppError(
            code="AUTHENTICATION_REQUIRED",
            message="用户未登录或账号已停用。",
            status_code=401,
        )
    return user


async def _locked_quota(session: AsyncSession, user_id, today: date) -> UserQuota:
    await session.execute(
        pg_insert(UserQuota)
        .values(user_id=user_id, current_count_date=today, question_count=0, upload_count=0)
        .on_conflict_do_nothing(index_elements=[UserQuota.user_id])
    )
    quota = await session.scalar(
        select(UserQuota).where(UserQuota.user_id == user_id).with_for_update()
    )
    assert quota is not None
    if quota.current_count_date != today:
        quota.current_count_date = today
        quota.question_count = 0
        quota.upload_count = 0
        await session.flush()
    return quota


async def consume_question(
    session: AsyncSession,
    *,
    user_id,
    defaults: QuotaDefaults,
    today: date | None = None,
) -> None:
    await _locked_active_user(session, user_id)
    quota = await _locked_quota(session, user_id, today or shanghai_date())
    if quota.question_count >= effective_limit(
        quota.daily_question_limit, defaults.daily_questions
    ):
        raise AppError(
            code="QUESTION_QUOTA_EXCEEDED", message="今日问答额度已用尽。", status_code=429
        )
    quota.question_count += 1
    await session.flush()


async def consume_upload(
    session: AsyncSession,
    *,
    user_id,
    content_bytes: int,
    defaults: QuotaDefaults,
    today: date | None = None,
) -> None:
    if content_bytes < 0:
        raise ValueError("content_bytes 不能为负数")
    await _locked_active_user(session, user_id)
    quota = await _locked_quota(session, user_id, today or shanghai_date())
    upload_limit = effective_limit(quota.daily_upload_limit, defaults.daily_uploads)
    if quota.upload_count >= upload_limit:
        raise AppError(
            code="UPLOAD_QUOTA_EXCEEDED", message="今日上传额度已用尽。", status_code=429
        )
    used_storage = int(
        await session.scalar(
            select(func.coalesce(func.sum(Document.file_size), 0)).where(
                Document.uploaded_by_user_id == user_id,
                Document.deleted_at.is_(None),
                Document.status != "failed",
            )
        )
        or 0
    )
    if used_storage + content_bytes > effective_limit(
        quota.storage_bytes_limit, defaults.storage_bytes
    ):
        raise AppError(code="STORAGE_QUOTA_EXCEEDED", message="有效存储额度不足。", status_code=429)
    quota.upload_count += 1
    await session.flush()


def _month_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    local_now = (now or datetime.now(UTC)).astimezone(_SHANGHAI)
    start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start.astimezone(UTC), end.astimezone(UTC)


async def reserve_global_cost(
    session: AsyncSession,
    *,
    new_cost: Decimal,
    limit: Decimal,
    now: datetime | None = None,
    replacing_event_id=None,
    replacing_cost: Decimal = Decimal("0"),
) -> None:
    validate_cost(new_cost)
    validate_cost(limit)
    validate_cost(replacing_cost)
    await session.execute(select(func.pg_advisory_xact_lock(_GLOBAL_COST_LOCK_KEY)))
    start, end = _month_bounds(now)
    total = await session.scalar(
        select(
            func.coalesce(
                func.sum(func.coalesce(LlmUsageEvent.settled_cost, LlmUsageEvent.reserved_cost)),
                Decimal("0"),
            )
        ).where(LlmUsageEvent.created_at >= start, LlmUsageEvent.created_at < end)
    )
    existing = Decimal(total or 0)
    if replacing_event_id is not None:
        existing -= replacing_cost
    if existing + new_cost > limit:
        raise AppError(
            code="GLOBAL_COST_LIMIT_REACHED", message="本月全局费用上限已达到。", status_code=429
        )


async def quota_snapshot(
    session: AsyncSession,
    *,
    user_id,
    defaults: QuotaDefaults,
    today: date | None = None,
) -> QuotaSnapshot:
    quota = await session.get(UserQuota, user_id)
    current_day = today or shanghai_date()
    counts_are_current = quota is not None and quota.current_count_date == current_day
    used_storage = int(
        await session.scalar(
            select(func.coalesce(func.sum(Document.file_size), 0)).where(
                Document.uploaded_by_user_id == user_id,
                Document.deleted_at.is_(None),
                Document.status != "failed",
            )
        )
        or 0
    )
    return QuotaSnapshot(
        daily_question_limit=effective_limit(
            quota.daily_question_limit if quota else None, defaults.daily_questions
        ),
        daily_upload_limit=effective_limit(
            quota.daily_upload_limit if quota else None, defaults.daily_uploads
        ),
        storage_bytes_limit=effective_limit(
            quota.storage_bytes_limit if quota else None, defaults.storage_bytes
        ),
        question_count=quota.question_count if counts_are_current and quota else 0,
        upload_count=quota.upload_count if counts_are_current and quota else 0,
        storage_bytes_used=used_storage,
    )

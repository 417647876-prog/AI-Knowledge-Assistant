from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import get_current_user
from app.api.v1.feedback import FeedbackReason
from app.db.models import (
    AnswerFeedback,
    Conversation,
    ConversationMessage,
    KnowledgeBase,
    LlmUsageEvent,
    User,
)
from app.db.session import get_session

router = APIRouter(prefix="/api/v1/me", tags=["me"])


class TokenSummary(BaseModel):
    cache_hit_input_tokens: int
    cache_miss_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    total_tokens: int


class PurposeUsageSummary(BaseModel):
    event_count: int
    total_tokens: int
    estimated_cost: Decimal
    usage_unknown_count: int


class UsageSummaryResponse(BaseModel):
    from_date: datetime = Field(serialization_alias="from")
    to_date: datetime = Field(serialization_alias="to")
    tokens: TokenSummary
    estimated_cost: Decimal
    usage_unknown_count: int
    purposes: dict[str, PurposeUsageSummary]


class FeedbackListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    message_id: UUID
    helpful: bool
    reason: FeedbackReason | None
    created_at: datetime
    updated_at: datetime


class FeedbackPage(BaseModel):
    items: list[FeedbackListItem]
    page: int
    page_size: int
    total: int


def _empty_purpose() -> dict[str, int | Decimal]:
    return {
        "event_count": 0,
        "total_tokens": 0,
        "estimated_cost": Decimal("0.000000"),
        "usage_unknown_count": 0,
    }


@router.get("/usage", response_model=UsageSummaryResponse)
async def get_usage_summary(
    from_date: Annotated[datetime, Query(alias="from")],
    to_date: Annotated[datetime, Query(alias="to")],
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> UsageSummaryResponse:
    if (
        from_date.tzinfo is None
        or from_date.utcoffset() is None
        or to_date.tzinfo is None
        or to_date.utcoffset() is None
    ):
        raise HTTPException(status_code=422, detail="from 和 to 必须包含时区")
    start = from_date.astimezone(UTC)
    end = to_date.astimezone(UTC)
    if start >= end:
        raise HTTPException(status_code=422, detail="from 必须早于 to")
    rows = (
        await session.execute(
            select(
                LlmUsageEvent.purpose,
                LlmUsageEvent.status,
                LlmUsageEvent.cache_hit_input_tokens,
                LlmUsageEvent.cache_miss_input_tokens,
                LlmUsageEvent.output_tokens,
                LlmUsageEvent.reasoning_tokens,
                LlmUsageEvent.total_tokens,
                LlmUsageEvent.usage_complete,
                LlmUsageEvent.settled_cost,
            ).where(
                LlmUsageEvent.user_id == current_user.id,
                LlmUsageEvent.created_at >= start,
                LlmUsageEvent.created_at < end,
                LlmUsageEvent.status != "reserved",
            )
        )
    ).all()
    token_values = {
        "cache_hit_input_tokens": 0,
        "cache_miss_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }
    purposes = {"answer": _empty_purpose(), "rewrite": _empty_purpose()}
    estimated_cost = Decimal("0.000000")
    usage_unknown_count = 0
    for row in rows:
        purpose = purposes[row.purpose]
        purpose["event_count"] += 1
        purpose["total_tokens"] += row.total_tokens
        settled_cost = row.settled_cost or Decimal("0.000000")
        purpose["estimated_cost"] += settled_cost
        estimated_cost += settled_cost
        is_unknown = row.status == "usage_unknown"
        if is_unknown:
            purpose["usage_unknown_count"] += 1
            usage_unknown_count += 1
        token_values["cache_hit_input_tokens"] += row.cache_hit_input_tokens
        token_values["cache_miss_input_tokens"] += row.cache_miss_input_tokens
        token_values["output_tokens"] += row.output_tokens
        token_values["reasoning_tokens"] += row.reasoning_tokens
        token_values["total_tokens"] += row.total_tokens
    return UsageSummaryResponse(
        from_date=start,
        to_date=end,
        tokens=TokenSummary(**token_values),
        estimated_cost=estimated_cost,
        usage_unknown_count=usage_unknown_count,
        purposes={purpose: PurposeUsageSummary(**values) for purpose, values in purposes.items()},
    )


@router.get("/feedback", response_model=FeedbackPage)
async def get_feedback_page(
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> FeedbackPage:
    filters = (
        AnswerFeedback.user_id == current_user.id,
        Conversation.user_id == current_user.id,
        KnowledgeBase.owner_id == current_user.id,
        KnowledgeBase.deleted_at.is_(None),
    )
    joins = (
        (ConversationMessage, ConversationMessage.id == AnswerFeedback.message_id),
        (Conversation, Conversation.id == ConversationMessage.conversation_id),
        (KnowledgeBase, KnowledgeBase.id == Conversation.knowledge_base_id),
    )
    count_statement = select(func.count()).select_from(AnswerFeedback)
    item_statement = select(AnswerFeedback)
    for target, condition in joins:
        count_statement = count_statement.join(target, condition)
        item_statement = item_statement.join(target, condition)
    total = int(await session.scalar(count_statement.where(*filters)) or 0)
    items = list(
        (
            await session.scalars(
                item_statement.where(*filters)
                .order_by(AnswerFeedback.updated_at.desc(), AnswerFeedback.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    return FeedbackPage(
        items=[FeedbackListItem.model_validate(item) for item in items],
        page=page,
        page_size=page_size,
        total=total,
    )

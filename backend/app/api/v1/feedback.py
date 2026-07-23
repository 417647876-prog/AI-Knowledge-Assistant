from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import get_current_user
from app.core.exceptions import AppError
from app.db.models import (
    AnswerFeedback,
    Conversation,
    ConversationMessage,
    KnowledgeBase,
    User,
)
from app.db.session import get_session

FeedbackReason = Literal[
    "helpful_clear",
    "helpful_cited",
    "unhelpful_wrong",
    "unhelpful_missing",
    "unhelpful_unclear",
]

router = APIRouter(prefix="/api/v1", tags=["feedback"])


class FeedbackRequest(BaseModel):
    helpful: bool
    reason: FeedbackReason | None = None

    @model_validator(mode="after")
    def validate_reason_matches_helpfulness(self) -> "FeedbackRequest":
        if self.reason is None:
            return self
        if self.helpful and not self.reason.startswith("helpful_"):
            raise ValueError("正向反馈只能使用 helpful 原因")
        if not self.helpful and not self.reason.startswith("unhelpful_"):
            raise ValueError("负向反馈只能使用 unhelpful 原因")
        return self


class FeedbackResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    message_id: UUID
    helpful: bool
    reason: FeedbackReason | None
    created_at: datetime
    updated_at: datetime


def _message_not_found() -> AppError:
    return AppError(
        code="MESSAGE_NOT_FOUND",
        message="消息不存在。",
        status_code=404,
    )


async def _lock_owned_feedback_target(
    session: AsyncSession,
    *,
    user_id: UUID,
    message_id: UUID,
) -> ConversationMessage:
    conversation = await session.scalar(
        select(Conversation)
        .join(
            ConversationMessage,
            ConversationMessage.conversation_id == Conversation.id,
        )
        .join(KnowledgeBase, KnowledgeBase.id == Conversation.knowledge_base_id)
        .where(
            ConversationMessage.id == message_id,
            Conversation.user_id == user_id,
            KnowledgeBase.owner_id == user_id,
            KnowledgeBase.deleted_at.is_(None),
        )
        .with_for_update(of=Conversation)
    )
    if conversation is None:
        raise _message_not_found()
    message = await session.scalar(
        select(ConversationMessage)
        .where(
            ConversationMessage.id == message_id,
            ConversationMessage.conversation_id == conversation.id,
            ConversationMessage.role == "assistant",
            ConversationMessage.status == "completed",
        )
        .with_for_update()
    )
    if message is None:
        raise _message_not_found()
    return message


@router.put(
    "/messages/{message_id}/feedback",
    response_model=FeedbackResponse,
)
async def put_feedback(
    message_id: UUID,
    payload: FeedbackRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> FeedbackResponse:
    await _lock_owned_feedback_target(
        session,
        user_id=current_user.id,
        message_id=message_id,
    )
    statement = (
        pg_insert(AnswerFeedback)
        .values(
            message_id=message_id,
            user_id=current_user.id,
            helpful=payload.helpful,
            reason=payload.reason,
        )
        .on_conflict_do_update(
            index_elements=[AnswerFeedback.user_id, AnswerFeedback.message_id],
            set_={
                "helpful": payload.helpful,
                "reason": payload.reason,
                "updated_at": func.now(),
            },
        )
        .returning(AnswerFeedback)
    )
    feedback = await session.scalar(statement)
    if feedback is None:
        raise RuntimeError("反馈写入失败")
    await session.commit()
    return FeedbackResponse.model_validate(feedback)


@router.delete(
    "/messages/{message_id}/feedback",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_feedback(
    message_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    await _lock_owned_feedback_target(
        session,
        user_id=current_user.id,
        message_id=message_id,
    )
    deleted_id = await session.scalar(
        delete(AnswerFeedback)
        .where(
            AnswerFeedback.user_id == current_user.id,
            AnswerFeedback.message_id == message_id,
        )
        .returning(AnswerFeedback.id)
    )
    if deleted_id is None:
        raise _message_not_found()
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

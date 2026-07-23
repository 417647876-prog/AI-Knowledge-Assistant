from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CreateConversationRequest(BaseModel):
    title: str = Field(default="新会话", min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("会话标题不能为空")
        return value


class ConversationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    knowledge_base_id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationPage(BaseModel):
    items: list[ConversationSummary]
    page: int
    page_size: int
    total: int


class ConversationMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sequence_number: int
    role: str
    content: str
    status: str
    retry_of_message_id: UUID | None
    citations_snapshot: list[dict[str, Any]]
    retrieval_stats: dict[str, Any]
    timings: dict[str, Any]
    finish_reason: str | None
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None


class ConversationDetail(ConversationSummary):
    messages: list[ConversationMessageResponse]


class StreamConversationMessageRequest(BaseModel):
    question: str | None = Field(default=None, min_length=1, max_length=2000)
    retry_of_message_id: UUID | None = None
    top_k: int | None = Field(default=None, ge=1, le=20)

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("问题不能为空")
        return value

    @model_validator(mode="after")
    def exactly_one_input(self):
        if (self.question is None) == (self.retry_of_message_id is None):
            raise ValueError("question 与 retry_of_message_id 必须且只能提供一个")
        return self

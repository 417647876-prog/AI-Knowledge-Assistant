from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ExpectedSource(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    contains: str = Field(min_length=1, max_length=500)

    @field_validator("file_name", "contains")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("期望来源字段不能为空")
        return value


class EvaluationTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("历史消息内容不能为空")
        return value


class EvaluationCase(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9-]+$", min_length=1, max_length=100)
    category: Literal["keyword", "semantic", "refusal", "multi_turn", "interference"]
    question: str = Field(min_length=1, max_length=2000)
    expected_sources: list[ExpectedSource] = Field(default_factory=list)
    should_refuse: bool = False
    history: list[EvaluationTurn] = Field(default_factory=list, max_length=12)

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("问题不能为空")
        return value

    @model_validator(mode="after")
    def validate_expected_sources(self) -> "EvaluationCase":
        if self.should_refuse and self.expected_sources:
            raise ValueError("拒答案例不能包含期望来源")
        if not self.should_refuse and not self.expected_sources:
            raise ValueError("非拒答案例必须包含期望来源")
        expected_role = "user"
        for turn in self.history:
            if turn.role != expected_role:
                raise ValueError("历史消息必须严格按照 user 和 assistant 成对排列")
            expected_role = "assistant" if expected_role == "user" else "user"
        if expected_role == "assistant":
            raise ValueError("历史消息必须以完整问答对结束")
        return self

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

EvaluationCategory = Literal[
    "keyword",
    "semantic",
    "refusal",
    "multi_turn",
    "interference",
]
GateStatus = Literal["passed", "failed", "waived"]


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
    category: EvaluationCategory
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


class EvaluationProvenance(BaseModel):
    run_id: UUID
    knowledge_base_id: UUID
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    generated_at: datetime


class CaseResult(BaseModel):
    case_id: str
    category: EvaluationCategory | None = None
    retrieved_files: list[str]
    citation_files: list[str]
    accepted_chunk_count: int = Field(ge=0)
    recall_at_k: float = Field(ge=0, le=1)
    reciprocal_rank: float = Field(ge=0, le=1)
    citation_hit_rate: float | None = Field(default=None, ge=0, le=1)
    refused: bool
    refusal_correct: bool
    latency_ms: float = Field(ge=0)


class EvaluationReport(BaseModel):
    schema_version: Literal["1.0", "1.1"] = "1.0"
    mode: Literal["vector", "hybrid", "rerank", "rewrite"]
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    top_k: int = Field(ge=5)
    case_count: int = Field(ge=1)
    recall_at_5: float = Field(ge=0, le=1)
    mrr_at_5: float = Field(ge=0, le=1)
    citation_hit_rate: float = Field(ge=0, le=1)
    refusal_accuracy: float = Field(ge=0, le=1)
    latency_p50_ms: float = Field(ge=0)
    latency_p95_ms: float = Field(ge=0)
    environment: dict[str, str] = Field(default_factory=dict)
    provenance: EvaluationProvenance | None = None
    cases: list[CaseResult]

    @model_validator(mode="after")
    def validate_report_contract(self) -> "EvaluationReport":
        if self.case_count != len(self.cases):
            raise ValueError("case_count 必须等于 cases 数量")
        if self.schema_version == "1.1":
            if self.provenance is None:
                raise ValueError("1.1 报告必须包含溯源信息")
            for case in self.cases:
                if case.category is None:
                    raise ValueError("1.1 案例必须包含 category")
                if case.citation_hit_rate is None:
                    raise ValueError("1.1 案例必须包含 citation_hit_rate")
        return self

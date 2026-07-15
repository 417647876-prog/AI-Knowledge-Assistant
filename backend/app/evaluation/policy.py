from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.evaluation.schemas import EvaluationCategory

ALLOWED_WAIVER_GATE_IDS = {"stage3c.mrr_relative_gain"}
REQUIRED_CATEGORIES: set[EvaluationCategory] = {
    "keyword",
    "semantic",
    "refusal",
    "multi_turn",
    "interference",
}


class HistoricalThresholds(BaseModel):
    stage3b_keyword_gain: float = Field(ge=0, le=1)
    stage3c_mrr_relative_gain: float = Field(ge=0, le=1)
    stage3d_multi_turn_gain: float = Field(ge=0, le=1)


class FinalThresholds(BaseModel):
    recall_at_5: float = Field(ge=0, le=1)
    citation_hit_rate: float = Field(ge=0, le=1)
    refusal_accuracy: float = Field(ge=0, le=1)


class QualityWaiver(BaseModel):
    gate_id: str
    approved_on: date
    minimum_allowed: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1)
    evidence: Path


class Stage3QualityPolicy(BaseModel):
    schema_version: Literal["1.0"]
    final_mode: Literal["rewrite"]
    minimum_case_count: int = Field(ge=30)
    required_categories: list[EvaluationCategory]
    historical_thresholds: HistoricalThresholds
    final_thresholds: FinalThresholds
    waivers: list[QualityWaiver]

    @model_validator(mode="after")
    def validate_policy(self) -> "Stage3QualityPolicy":
        if (
            len(self.required_categories) != len(REQUIRED_CATEGORIES)
            or set(self.required_categories) != REQUIRED_CATEGORIES
        ):
            raise ValueError("required_categories 必须恰好包含五个固定分类")
        waiver_ids = [waiver.gate_id for waiver in self.waivers]
        if len(set(waiver_ids)) != len(waiver_ids):
            raise ValueError("重复 waiver gate_id")
        if set(waiver_ids) != ALLOWED_WAIVER_GATE_IDS:
            raise ValueError("策略包含不允许豁免的 gate 或缺少 3C MRR 豁免")
        return self


def load_stage3_quality_policy(
    path: Path,
    *,
    repo_root: Path,
) -> Stage3QualityPolicy:
    policy = Stage3QualityPolicy.model_validate_json(path.read_text(encoding="utf-8"))
    resolved_root = repo_root.resolve()
    for waiver in policy.waivers:
        evidence = (resolved_root / waiver.evidence).resolve()
        if not evidence.is_relative_to(resolved_root):
            raise ValueError("豁免证据文件不在仓库内")
        if not evidence.is_file():
            raise ValueError("豁免证据文件不存在")
    return policy

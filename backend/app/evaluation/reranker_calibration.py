"""Reranker 接受阈值的独立校准契约。"""

from math import isfinite
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator


class CalibrationCase(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9-]+$", min_length=1, max_length=100)
    question: str = Field(min_length=1, max_length=2000)
    document: str = Field(min_length=1, max_length=8000)
    relevant: bool = Field(strict=True)

    @field_validator("question", "document", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class CalibrationReport(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    model_name: str
    device: str
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_count: int = Field(ge=2)
    positive_count: int = Field(ge=1)
    negative_count: int = Field(ge=1)
    score_min: float
    score_max: float
    recommended_min_score: float
    false_accept_rate: float = Field(ge=0, le=1)
    positive_accept_rate: float = Field(ge=0, le=1)


def load_calibration_cases(path: Path) -> list[CalibrationCase]:
    cases: list[CalibrationCase] = []
    seen_ids: set[str] = set()

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            case = CalibrationCase.model_validate_json(raw_line)
        except (ValidationError, ValueError):
            raise ValueError("校准数据集格式无效") from None
        if case.id in seen_ids:
            raise ValueError("校准案例 ID 不能重复")
        seen_ids.add(case.id)
        cases.append(case)

    if not cases:
        raise ValueError("校准数据集不能为空")
    if not any(case.relevant for case in cases) or not any(not case.relevant for case in cases):
        raise ValueError("校准数据集必须同时包含正样本和负样本")
    return cases


def select_acceptance_threshold(
    cases: list[CalibrationCase],
    scores: list[float],
    model_name: str,
    device: str,
    dataset_sha256: str,
    min_positive_accept_rate: float = 0.8,
) -> CalibrationReport:
    if len(cases) != len(scores):
        raise ValueError("校准案例与分数数量必须一致")
    if not isfinite(min_positive_accept_rate) or not 0 <= min_positive_accept_rate <= 1:
        raise ValueError("正样本接受率约束必须位于 0 到 1 之间")

    try:
        normalized_scores = [float(score) for score in scores]
    except (TypeError, ValueError):
        raise ValueError("校准分数必须为有限数值") from None
    if not all(isfinite(score) for score in normalized_scores):
        raise ValueError("校准分数必须为有限数值")

    positive_count = sum(case.relevant for case in cases)
    negative_count = len(cases) - positive_count
    if positive_count == 0 or negative_count == 0:
        raise ValueError("校准案例必须同时包含正样本和负样本")

    unique_scores = sorted(set(normalized_scores))
    candidate_thresholds: list[float] = []
    for lower, upper in zip(unique_scores, unique_scores[1:], strict=False):
        threshold = lower + (upper - lower) / 2
        if not isfinite(threshold):
            raise ValueError("候选阈值必须为有限数值")
        candidate_thresholds.append(threshold)

    for threshold in candidate_thresholds:
        accepted_negative_count = sum(
            not case.relevant and score >= threshold
            for case, score in zip(cases, normalized_scores, strict=True)
        )
        accepted_positive_count = sum(
            case.relevant and score >= threshold
            for case, score in zip(cases, normalized_scores, strict=True)
        )
        false_accept_rate = accepted_negative_count / negative_count
        positive_accept_rate = accepted_positive_count / positive_count
        if false_accept_rate == 0.0 and positive_accept_rate >= min_positive_accept_rate:
            return CalibrationReport(
                model_name=model_name,
                device=device,
                dataset_sha256=dataset_sha256,
                case_count=len(cases),
                positive_count=positive_count,
                negative_count=negative_count,
                score_min=min(normalized_scores),
                score_max=max(normalized_scores),
                recommended_min_score=threshold,
                false_accept_rate=false_accept_rate,
                positive_accept_rate=positive_accept_rate,
            )
    raise ValueError("不存在满足约束的 Reranker 接受阈值")

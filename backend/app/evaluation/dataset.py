from pathlib import Path

from pydantic import ValidationError

from app.evaluation.schemas import EvaluationCase


def load_evaluation_cases(path: Path) -> list[EvaluationCase]:
    cases: list[EvaluationCase] = []
    seen_ids: set[str] = set()

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            case = EvaluationCase.model_validate_json(raw_line)
        except ValidationError as error:
            raise ValueError(f"评估数据第 {line_number} 行格式错误") from error
        if case.id in seen_ids:
            raise ValueError(f"重复评估 ID: {case.id}")
        seen_ids.add(case.id)
        cases.append(case)

    if not cases:
        raise ValueError("评估数据集不能为空")
    return cases

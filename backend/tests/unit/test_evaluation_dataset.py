from collections import Counter
from pathlib import Path

import pytest

from app.evaluation.dataset import load_evaluation_cases

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "evaluation" / "stage3.jsonl"


def test_load_evaluation_cases_reads_the_stage3_fixture() -> None:
    cases = load_evaluation_cases(FIXTURE_PATH)

    assert len(cases) == 30
    assert len({case.id for case in cases}) == 30
    assert Counter(case.category for case in cases) == {
        "keyword": 6,
        "semantic": 8,
        "refusal": 6,
        "multi_turn": 6,
        "interference": 4,
    }
    assert all(not case.expected_sources for case in cases if case.should_refuse)
    assert all(case.expected_sources for case in cases if not case.should_refuse)


def test_load_evaluation_cases_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        '{"id":"same","category":"refusal","question":"甲","expected_sources":[],"should_refuse":true}\n'
        '{"id":"same","category":"refusal","question":"乙","expected_sources":[],"should_refuse":true}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="重复评估 ID: same"):
        load_evaluation_cases(path)


def test_load_evaluation_cases_rejects_an_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("\n", encoding="utf-8")

    with pytest.raises(ValueError, match="评估数据集不能为空"):
        load_evaluation_cases(path)


def test_load_evaluation_cases_reports_the_invalid_json_line(tmp_path: Path) -> None:
    path = tmp_path / "invalid.jsonl"
    path.write_text("{not-json}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="第 1 行"):
        load_evaluation_cases(path)

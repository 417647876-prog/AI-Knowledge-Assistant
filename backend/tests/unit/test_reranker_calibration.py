import json
from pathlib import Path

import pytest

from app.evaluation import reranker_calibration
from app.evaluation.reranker_calibration import CalibrationCase, load_calibration_cases


def write_dataset(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def test_load_calibration_cases_preserves_order_and_strips_text(tmp_path: Path) -> None:
    dataset = tmp_path / "calibration.jsonl"
    write_dataset(
        dataset,
        [
            '{"id":"positive-1","question":"  年假期限  ",'
            '"document":"  年假五天  ","relevant":true}',
            '{"id":"negative-1","question":"年假期限","document":"密码十二位","relevant":false}',
        ],
    )

    cases = load_calibration_cases(dataset)

    assert [case.id for case in cases] == ["positive-1", "negative-1"]
    assert cases[0].question == "年假期限"
    assert cases[0].document == "年假五天"


@pytest.mark.parametrize(
    ("lines", "message"),
    [
        ([], "校准数据集不能为空"),
        (
            [
                '{"id":"same-id","question":"年假期限","document":"年假五天","relevant":true}',
                '{"id":"same-id","question":"密码要求","document":"密码十二位","relevant":false}',
            ],
            "校准案例 ID 不能重复",
        ),
        (
            ['{"id":"positive-1","question":"年假期限","document":"年假五天","relevant":true}'],
            "正样本和负样本",
        ),
        (
            ['{"id":"negative-1","question":"年假期限","document":"密码十二位","relevant":false}'],
            "正样本和负样本",
        ),
    ],
)
def test_load_calibration_cases_rejects_invalid_dataset_shape(
    tmp_path: Path, lines: list[str], message: str
) -> None:
    dataset = tmp_path / "calibration.jsonl"
    write_dataset(dataset, lines)

    with pytest.raises(ValueError, match=message):
        load_calibration_cases(dataset)


@pytest.mark.parametrize("invalid_relevant", ['"true"', "1", "null"])
def test_load_calibration_cases_requires_boolean_relevant(
    tmp_path: Path, invalid_relevant: str
) -> None:
    dataset = tmp_path / "calibration.jsonl"
    write_dataset(
        dataset,
        [
            '{"id":"invalid-1","question":"年假期限","document":"secret-document",'
            f'"relevant":{invalid_relevant}}}',
            '{"id":"negative-1","question":"年假期限","document":"密码十二位","relevant":false}',
        ],
    )

    with pytest.raises(ValueError, match="校准数据集格式无效") as raised:
        load_calibration_cases(dataset)

    assert "secret-document" not in str(raised.value)


def test_load_calibration_cases_sanitizes_validation_error(tmp_path: Path) -> None:
    dataset = tmp_path / "calibration.jsonl"
    secret = "private-document-content"
    write_dataset(
        dataset,
        [
            f'{{"id":"INVALID","question":"年假期限","document":"{secret}","relevant":true}}',
            '{"id":"negative-1","question":"年假期限","document":"密码十二位","relevant":false}',
        ],
    )

    with pytest.raises(ValueError, match="校准数据集格式无效") as raised:
        load_calibration_cases(dataset)

    assert secret not in str(raised.value)


def make_cases(labels: list[bool]) -> list[CalibrationCase]:
    return [
        CalibrationCase(
            id=f"case-{index}",
            question=f"问题 {index}",
            document=f"文档 {index}",
            relevant=relevant,
        )
        for index, relevant in enumerate(labels)
    ]


def test_select_acceptance_threshold_chooses_lowest_feasible_midpoint() -> None:
    cases = make_cases([False, False, True, True, True, True, True])

    report = reranker_calibration.select_acceptance_threshold(
        cases,
        [-3.0, -1.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        model_name="BAAI/test",
        device="cpu",
        dataset_sha256="a" * 64,
    )

    assert report.recommended_min_score == pytest.approx(-0.4)
    assert report.false_accept_rate == 0.0
    assert report.positive_accept_rate == 1.0


def test_select_acceptance_threshold_fails_when_constraints_conflict() -> None:
    cases = make_cases([False, True, True, True, True, True])

    with pytest.raises(ValueError, match="不存在满足约束"):
        reranker_calibration.select_acceptance_threshold(
            cases,
            [0.9, 0.1, 0.2, 0.3, 0.4, 0.5],
            model_name="BAAI/test",
            device="cpu",
            dataset_sha256="b" * 64,
        )


def test_select_acceptance_threshold_rejects_score_count_mismatch() -> None:
    cases = make_cases([False, True])

    with pytest.raises(ValueError, match="分数数量") as raised:
        reranker_calibration.select_acceptance_threshold(
            cases,
            [0.5],
            model_name="BAAI/test",
            device="cpu",
            dataset_sha256="c" * 64,
        )

    assert "问题 0" not in str(raised.value)
    assert "0.5" not in str(raised.value)


@pytest.mark.parametrize("invalid_score", [float("nan"), float("inf"), float("-inf")])
def test_select_acceptance_threshold_rejects_non_finite_scores(
    invalid_score: float,
) -> None:
    cases = make_cases([False, True])

    with pytest.raises(ValueError, match="分数必须为有限数值") as raised:
        reranker_calibration.select_acceptance_threshold(
            cases,
            [invalid_score, 1.0],
            model_name="BAAI/test",
            device="cpu",
            dataset_sha256="d" * 64,
        )

    assert str(invalid_score) not in str(raised.value)
    assert "文档 0" not in str(raised.value)


@pytest.mark.parametrize("invalid_rate", [-0.01, 1.01, float("nan")])
def test_select_acceptance_threshold_rejects_invalid_positive_rate(
    invalid_rate: float,
) -> None:
    with pytest.raises(ValueError, match="正样本接受率约束"):
        reranker_calibration.select_acceptance_threshold(
            make_cases([False, True]),
            [-1.0, 1.0],
            model_name="BAAI/test",
            device="cpu",
            dataset_sha256="e" * 64,
            min_positive_accept_rate=invalid_rate,
        )


def test_select_acceptance_threshold_rejects_non_finite_midpoint() -> None:
    with pytest.raises(ValueError, match="候选阈值必须为有限数值"):
        reranker_calibration.select_acceptance_threshold(
            make_cases([False, True]),
            [-1e308, 1e308],
            model_name="BAAI/test",
            device="cpu",
            dataset_sha256="f" * 64,
        )


def test_stage3c_calibration_dataset_has_balanced_independent_question_pairs() -> None:
    fixtures = Path(__file__).parents[1] / "fixtures"
    calibration_path = fixtures / "evaluation" / "stage3c-reranker-calibration.jsonl"
    stage3_path = fixtures / "evaluation" / "stage3.jsonl"

    cases = load_calibration_cases(calibration_path)
    calibration_questions = {case.question for case in cases}
    stage3_questions = {
        json.loads(line)["question"]
        for line in stage3_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    labels_by_question = {
        question: {case.relevant for case in cases if case.question == question}
        for question in calibration_questions
    }

    assert len(cases) >= 20
    assert len(calibration_questions) >= 10
    assert sum(case.relevant for case in cases) >= 10
    assert sum(not case.relevant for case in cases) >= 10
    assert all(labels == {False, True} for labels in labels_by_question.values())
    assert calibration_questions.isdisjoint(stage3_questions)


def test_stage3c_calibration_dataset_covers_required_cases_with_test_documents() -> None:
    fixtures = Path(__file__).parents[1] / "fixtures"
    cases = load_calibration_cases(fixtures / "evaluation" / "stage3c-reranker-calibration.jsonl")
    generator_source = (fixtures / "generate_sample_documents.py").read_text(encoding="utf-8")

    assert all(case.document in generator_source for case in cases)
    assert all(
        any(marker in case.id for case in cases)
        for marker in ("policy", "semantic", "obvious", "lexical")
    )

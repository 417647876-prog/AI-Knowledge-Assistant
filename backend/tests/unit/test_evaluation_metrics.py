from uuid import uuid4

import pytest

from app.evaluation.metrics import (
    ceiling_aware_target,
    citation_hit_rate,
    percentile,
    recall_at_k,
    reciprocal_rank_at_k,
    refusal_is_correct,
)
from app.evaluation.schemas import ExpectedSource
from app.rag.schemas import Citation, RetrievedChunk


def _chunk(file_name: str, content: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        file_name=file_name,
        content=content,
        relevance_score=0.9,
    )


def _citation(file_name: str, content: str) -> Citation:
    return Citation(
        citation_id=1,
        document_id=uuid4(),
        file_name=file_name,
        content=content,
        relevance_score=0.9,
    )


def test_recall_and_mrr_use_ranked_expected_sources() -> None:
    expected = [
        ExpectedSource(file_name="年假制度.txt", contains="五天年假"),
        ExpectedSource(file_name="员工手册.docx", contains="补卡说明"),
    ]
    actual = [
        _chunk("无关.txt", "无关内容"),
        _chunk("年假制度.txt", "员工可享受五天年假。"),
        _chunk("员工手册.docx", "迟到后提交补卡说明。"),
    ]

    assert recall_at_k(expected, actual, 2) == 0.5
    assert reciprocal_rank_at_k(expected, actual, 3) == 0.5


def test_refusal_cases_require_an_empty_retrieval_result() -> None:
    assert recall_at_k([], [], 5) == 1.0
    assert reciprocal_rank_at_k([], [], 5) == 1.0
    assert recall_at_k([], [_chunk("资料.txt", "内容")], 5) == 0.0
    assert reciprocal_rank_at_k([], [_chunk("资料.txt", "内容")], 5) == 0.0


def test_citation_and_refusal_metrics_only_accept_expected_behavior() -> None:
    expected = [ExpectedSource(file_name="年假制度.txt", contains="五天年假")]

    assert citation_hit_rate(expected, [_citation("年假制度.txt", "五天年假")]) == 1.0
    assert citation_hit_rate(expected, [_citation("其他资料.txt", "五天年假")]) == 0.0
    assert refusal_is_correct(should_refuse=True, refused=True)
    assert not refusal_is_correct(should_refuse=True, refused=False)
    assert refusal_is_correct(should_refuse=False, refused=False)


@pytest.mark.parametrize("function", [recall_at_k, reciprocal_rank_at_k])
def test_rank_metrics_reject_non_positive_k(function) -> None:
    with pytest.raises(ValueError, match="k 必须大于 0"):
        function([], [], 0)


def test_percentile_uses_linear_interpolation() -> None:
    assert percentile([10.0, 20.0, 40.0, 100.0], 0.5) == 30.0
    assert percentile([10.0, 20.0, 40.0, 100.0], 0.95) == pytest.approx(91.0)


@pytest.mark.parametrize("values, quantile", [([], 0.5), ([1.0], -0.1), ([1.0], 1.1)])
def test_percentile_rejects_invalid_inputs(values: list[float], quantile: float) -> None:
    with pytest.raises(ValueError):
        percentile(values, quantile)


@pytest.mark.parametrize(
    ("baseline", "required_gain", "expected"),
    [
        (0.50, 0.15, 0.65),
        (0.93, 0.15, 1.00),
        (1.00, 0.15, 1.00),
        (0.93, 0.05, 0.98),
    ],
)
def test_ceiling_aware_target_never_exceeds_one(
    baseline: float,
    required_gain: float,
    expected: float,
) -> None:
    assert ceiling_aware_target(baseline, required_gain) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("baseline", "required_gain"),
    [(-0.01, 0.05), (1.01, 0.05), (0.50, -0.01), (0.50, 1.01)],
)
def test_ceiling_aware_target_rejects_invalid_ratios(
    baseline: float,
    required_gain: float,
) -> None:
    with pytest.raises(ValueError):
        ceiling_aware_target(baseline, required_gain)

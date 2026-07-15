from collections.abc import Sequence

from app.evaluation.schemas import ExpectedSource
from app.rag.schemas import Citation, RetrievedChunk


def ceiling_aware_target(baseline: float, required_gain: float) -> float:
    if not 0 <= baseline <= 1:
        raise ValueError("baseline 必须位于 0 到 1 之间")
    if not 0 <= required_gain <= 1:
        raise ValueError("required_gain 必须位于 0 到 1 之间")
    return min(1.0, baseline + required_gain)


def relative_gain(baseline: float, candidate: float) -> float:
    if not 0 <= baseline <= 1 or not 0 <= candidate <= 1:
        raise ValueError("baseline 和 candidate 必须位于 0 到 1 之间")
    if baseline == 0:
        return 0.0 if candidate == 0 else 1.0
    return (candidate - baseline) / baseline


def _matches(expected: ExpectedSource, file_name: str, content: str) -> bool:
    return expected.file_name == file_name and expected.contains in content


def _validate_k(k: int) -> None:
    if k < 1:
        raise ValueError("k 必须大于 0")


def recall_at_k(
    expected: Sequence[ExpectedSource], actual: Sequence[RetrievedChunk], k: int
) -> float:
    _validate_k(k)
    ranked = actual[:k]
    if not expected:
        return 1.0 if not ranked else 0.0
    matched = sum(
        any(_matches(source, chunk.file_name, chunk.content) for chunk in ranked)
        for source in expected
    )
    return matched / len(expected)


def reciprocal_rank_at_k(
    expected: Sequence[ExpectedSource], actual: Sequence[RetrievedChunk], k: int
) -> float:
    _validate_k(k)
    ranked = actual[:k]
    if not expected:
        return 1.0 if not ranked else 0.0
    for rank, chunk in enumerate(ranked, start=1):
        if any(_matches(source, chunk.file_name, chunk.content) for source in expected):
            return 1 / rank
    return 0.0


def citation_hit_rate(expected: Sequence[ExpectedSource], citations: Sequence[Citation]) -> float:
    if not expected:
        return 1.0 if not citations else 0.0
    if not citations:
        return 0.0
    matched = sum(
        any(_matches(source, citation.file_name, citation.content) for source in expected)
        for citation in citations
    )
    return matched / len(citations)


def refusal_is_correct(*, should_refuse: bool, refused: bool) -> bool:
    return should_refuse is refused


def percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        raise ValueError("百分位计算至少需要一个值")
    if not 0 <= quantile <= 1:
        raise ValueError("quantile 必须位于 0 到 1 之间")
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    fraction = position - lower_index
    return ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * fraction

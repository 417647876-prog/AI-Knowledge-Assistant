from uuid import uuid4

from app.rag.schemas import RetrievedChunk
from app.rag.streaming import CitationTracker, StreamEvent, citation_payload, encode_sse


def chunk(name: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        file_name=name,
        content="证据",
        relevance_score=0.9,
    )


def test_tracker_recognizes_cross_chunk_markers_once_and_ignores_out_of_range() -> None:
    tracker = CitationTracker([chunk("一.txt"), chunk("二.txt")])

    assert tracker.feed("结论 [") == []
    assert [item.citation_id for item in tracker.feed("1]")] == [1]
    assert tracker.feed(" 再次 [1] 与无效 [99]") == []
    assert [item.citation_id for item in tracker.feed("，补充 [2]。")] == [2]
    assert [item.citation_id for item in tracker.finish()] == [1, 2]


def test_sse_encoder_keeps_chinese_and_uses_single_json_data_line() -> None:
    encoded = encode_sse(StreamEvent("token", {"delta": "中文\n换行"})).decode()

    assert encoded.startswith("event: token\ndata: ")
    assert "中文" in encoded
    assert encoded.endswith("\n\n")
    assert encoded.count("data:") == 1


def test_citation_payload_serializes_uuid() -> None:
    tracker = CitationTracker([chunk("制度.txt")])

    citation = tracker.feed("答案。[1]")[0]

    assert citation_payload(citation)["document_id"] == str(citation.document_id)

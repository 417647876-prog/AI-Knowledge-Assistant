import hashlib

from app.knowledge.chunking import RecursiveTextChunker
from app.knowledge.schemas import ParsedSection


def test_chunker_preserves_source_and_limits_length() -> None:
    section = ParsedSection(
        text="第一句话。第二句话。第三句话。第四句话。",
        page_number=3,
        section_title="制度",
        metadata={"kind": "policy"},
    )

    chunks = RecursiveTextChunker(chunk_size=10, chunk_overlap=2).split([section])

    assert len(chunks) > 1
    assert all(0 < len(chunk.content) <= 10 for chunk in chunks)
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.page_number == 3 for chunk in chunks)
    assert all(chunk.section_title == "制度" for chunk in chunks)
    assert all(chunk.metadata == {"kind": "policy"} for chunk in chunks)
    assert all(
        chunk.content_hash == hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()
        for chunk in chunks
    )


def test_chunker_overlaps_chunks_and_records_start_index() -> None:
    chunks = RecursiveTextChunker(chunk_size=6, chunk_overlap=2).split(
        [ParsedSection(text="甲乙丙丁戊己庚辛壬癸")]
    )

    assert [chunk.content for chunk in chunks] == ["甲乙丙丁戊己", "戊己庚辛壬癸"]
    assert [chunk.start_index for chunk in chunks] == [0, 4]


def test_chunker_filters_empty_sections_and_does_not_overlap_sources() -> None:
    chunks = RecursiveTextChunker(chunk_size=6, chunk_overlap=2).split(
        [
            ParsedSection(text="   ", page_number=1),
            ParsedSection(text="第一页内容", page_number=2),
            ParsedSection(text="第二页内容", page_number=3),
        ]
    )

    assert [chunk.content for chunk in chunks] == ["第一页内容", "第二页内容"]
    assert [chunk.page_number for chunk in chunks] == [2, 3]
    assert [chunk.start_index for chunk in chunks] == [0, 0]

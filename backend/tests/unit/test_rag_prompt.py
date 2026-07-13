from uuid import uuid4

from app.rag.citations import map_citations
from app.rag.prompt import build_rag_prompt
from app.rag.schemas import RetrievedChunk


def _chunk(index: int, *, page_number: int | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        file_name=f"制度{index}.pdf",
        content=f"第{index}条制度内容",
        relevance_score=0.9 - index / 100,
        page_number=page_number,
        section_title="休假制度",
    )


def test_build_prompt_numbers_context_and_includes_real_source() -> None:
    system_prompt, user_prompt = build_rag_prompt("年假有几天？", [_chunk(1, page_number=12)])

    assert "只能依据" in system_prompt
    assert "[1]" in user_prompt
    assert "制度1.pdf" in user_prompt
    assert "页码：12" in user_prompt
    assert "第1条制度内容" in user_prompt
    assert "年假有几天？" in user_prompt


def test_map_citations_ignores_unknown_numbers_and_preserves_answer_order() -> None:
    chunks = [_chunk(1), _chunk(2)]

    citations = map_citations("先看[2]，再看[99]，最后重复[2]和[1]。", chunks)

    assert [item.citation_id for item in citations] == [2, 1]
    assert citations[0].document_id == chunks[1].document_id
    assert citations[1].document_id == chunks[0].document_id

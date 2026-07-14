import re

from app.rag.schemas import Citation, RetrievedChunk

_CITATION_PATTERN = re.compile(r"\[(\d+)\]")


def map_citations(answer: str, chunks: list[RetrievedChunk]) -> list[Citation]:
    numbers: list[int] = []
    for value in _CITATION_PATTERN.findall(answer):
        number = int(value)
        if 1 <= number <= len(chunks) and number not in numbers:
            numbers.append(number)

    return [
        Citation(
            citation_id=number,
            document_id=chunks[number - 1].document_id,
            file_name=chunks[number - 1].file_name,
            content=chunks[number - 1].content,
            relevance_score=chunks[number - 1].relevance_score,
            page_number=chunks[number - 1].page_number,
            sheet_name=chunks[number - 1].sheet_name,
            row_start=chunks[number - 1].row_start,
            section_title=chunks[number - 1].section_title,
        )
        for number in numbers
    ]

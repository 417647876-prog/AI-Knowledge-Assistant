import hashlib
from dataclasses import dataclass, field
from typing import Any

from app.knowledge.cleaning import clean_text
from app.knowledge.schemas import ParsedSection

SEPARATORS = ("\n\n", "\n", "。", "！", "？", "；", "，", "、", " ")


@dataclass(frozen=True)
class TextChunk:
    content: str
    chunk_index: int
    content_hash: str
    start_index: int
    page_number: int | None = None
    sheet_name: str | None = None
    row_start: int | None = None
    section_title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class RecursiveTextChunker:
    def __init__(self, *, chunk_size: int, chunk_overlap: int) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size 必须大于 0")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap 必须大于等于 0 且小于 chunk_size")
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    def split(self, sections: list[ParsedSection]) -> list[TextChunk]:
        chunks: list[TextChunk] = []
        for section in sections:
            content = clean_text(section.text)
            if not content:
                continue
            for piece, start_index in self._split_text(content):
                chunks.append(
                    TextChunk(
                        content=piece,
                        chunk_index=len(chunks),
                        content_hash=hashlib.sha256(piece.encode("utf-8")).hexdigest(),
                        start_index=start_index,
                        page_number=section.page_number,
                        sheet_name=section.sheet_name,
                        row_start=section.row_start,
                        section_title=section.section_title,
                        metadata=dict(section.metadata),
                    )
                )
        return chunks

    def _split_text(self, text: str) -> list[tuple[str, int]]:
        pieces: list[tuple[str, int]] = []
        start = 0
        while start < len(text):
            maximum_end = min(start + self._chunk_size, len(text))
            end = self._natural_end(text, start, maximum_end)
            piece = text[start:end].strip()
            if piece:
                actual_start = start + len(text[start:end]) - len(text[start:end].lstrip())
                pieces.append((piece, actual_start))
            if end >= len(text):
                break
            start = max(end - self._chunk_overlap, start + 1)
        return pieces

    @staticmethod
    def _natural_end(text: str, start: int, maximum_end: int) -> int:
        if maximum_end >= len(text):
            return len(text)
        window = text[start:maximum_end]
        for separator in SEPARATORS:
            position = window.rfind(separator)
            if position > 0:
                return start + position + len(separator)
        return maximum_end

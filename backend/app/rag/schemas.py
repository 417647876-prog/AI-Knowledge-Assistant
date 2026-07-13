from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: UUID
    document_id: UUID
    file_name: str
    content: str
    relevance_score: float
    page_number: int | None = None
    sheet_name: str | None = None
    row_start: int | None = None
    section_title: str | None = None

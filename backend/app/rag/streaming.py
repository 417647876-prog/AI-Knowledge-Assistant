import json
from dataclasses import asdict, dataclass

from app.rag.citations import map_citations
from app.rag.schemas import Citation, RetrievedChunk


@dataclass(frozen=True)
class StreamEvent:
    event: str
    data: dict[str, object]


class CitationTracker:
    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self._chunks = chunks
        self._answer = ""
        self._seen: set[int] = set()

    def feed(self, delta: str) -> list[Citation]:
        self._answer += delta
        current = map_citations(self._answer, self._chunks)
        new_items = [item for item in current if item.citation_id not in self._seen]
        self._seen.update(item.citation_id for item in new_items)
        return new_items

    def finish(self) -> list[Citation]:
        return map_citations(self._answer, self._chunks)


def citation_payload(citation: Citation) -> dict[str, object]:
    payload = asdict(citation)
    payload["document_id"] = str(citation.document_id)
    return payload


def encode_sse(event: StreamEvent) -> bytes:
    data = json.dumps(event.data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event.event}\ndata: {data}\n\n".encode()

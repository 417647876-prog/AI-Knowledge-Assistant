from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ParsedSection:
    text: str
    page_number: int | None = None
    sheet_name: str | None = None
    row_start: int | None = None
    section_title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

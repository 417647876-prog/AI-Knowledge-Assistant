from pathlib import Path
from typing import Protocol

from app.knowledge.schemas import ParsedSection


class DocumentParser(Protocol):
    def parse(self, file_path: Path) -> list[ParsedSection]: ...

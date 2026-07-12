from pathlib import Path

from app.core.exceptions import AppError
from app.knowledge.schemas import ParsedSection


class MarkdownParser:
    def parse(self, file_path: Path) -> list[ParsedSection]:
        text = file_path.read_text(encoding="utf-8-sig")
        sections: list[ParsedSection] = []
        title: str | None = None
        lines: list[str] = []
        for line in text.splitlines():
            if line.startswith("#") and line.lstrip("#").startswith(" "):
                if "\n".join(lines).strip():
                    sections.append(
                        ParsedSection(text="\n".join(lines).strip(), section_title=title)
                    )
                title, lines = line.lstrip("#").strip(), []
            else:
                lines.append(line)
        if "\n".join(lines).strip():
            sections.append(ParsedSection(text="\n".join(lines).strip(), section_title=title))
        if not sections:
            raise AppError(code="DOCUMENT_CONTENT_EMPTY", message="文档内容为空。", status_code=422)
        return sections

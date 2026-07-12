from pathlib import Path

import pytest

from app.core.exceptions import AppError
from app.knowledge.parsers.registry import ParserRegistry
from app.knowledge.schemas import ParsedSection


class TextParser:
    def parse(self, file_path: Path) -> list[ParsedSection]:
        return [ParsedSection(text=file_path.read_text(encoding="utf-8"))]


def test_registry_returns_registered_parser_and_parsed_section() -> None:
    parser = TextParser()
    registry = ParserRegistry({".txt": parser})

    result = registry.get_parser(".TXT")

    assert result is parser
    assert (
        ParsedSection(
            text="正文",
            page_number=1,
            sheet_name="数据",
            row_start=2,
            section_title="标题",
            metadata={"source": "fixture"},
        ).page_number
        == 1
    )


def test_registry_rejects_unsupported_extension() -> None:
    registry = ParserRegistry({})

    with pytest.raises(AppError) as error:
        registry.get_parser(".exe")

    assert error.value.code == "UNSUPPORTED_FILE_TYPE"

from pathlib import Path

import fitz

from app.core.exceptions import AppError
from app.knowledge.schemas import ParsedSection


class PdfParser:
    def parse(self, file_path: Path) -> list[ParsedSection]:
        with fitz.open(file_path) as document:
            sections = [
                ParsedSection(text=page.get_text().strip(), page_number=index + 1)
                for index, page in enumerate(document)
                if page.get_text().strip()
            ]
        if not sections:
            raise AppError(
                code="OCR_REQUIRED", message="PDF 没有可提取的文字，需要 OCR。", status_code=422
            )
        return sections

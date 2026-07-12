from pathlib import Path

from app.core.exceptions import AppError
from app.knowledge.schemas import ParsedSection


class TextParser:
    def parse(self, file_path: Path) -> list[ParsedSection]:
        try:
            text = file_path.read_text(encoding="utf-8-sig").strip()
        except UnicodeDecodeError as error:
            raise AppError(
                code="DOCUMENT_CONTENT_EMPTY", message="文本文件编码不受支持。", status_code=422
            ) from error
        if not text:
            raise AppError(code="DOCUMENT_CONTENT_EMPTY", message="文档内容为空。", status_code=422)
        return [ParsedSection(text=text)]

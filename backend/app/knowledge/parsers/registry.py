from app.core.exceptions import AppError
from app.knowledge.parsers.base import DocumentParser


class ParserRegistry:
    def __init__(self, parsers: dict[str, DocumentParser]) -> None:
        self._parsers = {extension.lower(): parser for extension, parser in parsers.items()}

    def get_parser(self, extension: str) -> DocumentParser:
        parser = self._parsers.get(extension.lower())
        if parser is None:
            raise AppError(
                code="UNSUPPORTED_FILE_TYPE",
                message="当前不支持该文件格式。",
                status_code=415,
            )
        return parser

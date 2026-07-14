from app.knowledge.parsers.excel import ExcelParser
from app.knowledge.parsers.markdown import MarkdownParser
from app.knowledge.parsers.pdf import PdfParser
from app.knowledge.parsers.registry import ParserRegistry
from app.knowledge.parsers.text import TextParser
from app.knowledge.parsers.word import WordParser


def create_parser_registry() -> ParserRegistry:
    return ParserRegistry(
        {
            ".txt": TextParser(),
            ".md": MarkdownParser(),
            ".pdf": PdfParser(),
            ".docx": WordParser(),
            ".xlsx": ExcelParser(),
        }
    )

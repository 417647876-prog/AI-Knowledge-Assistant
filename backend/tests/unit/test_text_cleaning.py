from app.knowledge.cleaning import clean_text


def test_clean_text_normalizes_lines_without_changing_meaning() -> None:
    source = "  第一行\r\n\r\n\r\n 第二行  "

    assert clean_text(source) == "第一行\n\n第二行"


def test_clean_text_returns_empty_for_whitespace_only_text() -> None:
    assert clean_text(" \r\n\t ") == ""

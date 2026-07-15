from app.knowledge.search_tokens import build_search_text


def test_build_search_text_handles_chinese_codes_and_words() -> None:
    result = build_search_text("VPN 账号连续输错 5 次将锁定")

    assert result.split() == [
        "vpn",
        "账号",
        "号连",
        "连续",
        "续输",
        "输错",
        "5",
        "次将",
        "将锁",
        "锁定",
    ]


def test_build_search_text_deduplicates_and_keeps_single_chinese_character() -> None:
    result = build_search_text("A a A1 A1 中，中")

    assert result == "a a1 中"


def test_build_search_text_returns_empty_for_text_without_searchable_characters() -> None:
    assert build_search_text(" \t，。！？\n") == ""

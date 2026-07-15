import re

_SEARCH_PART_PATTERN = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+")


def build_search_text(text: str) -> str:
    """生成供 PostgreSQL ``simple`` 配置使用的稳定检索 Token。"""
    tokens: list[str] = []
    seen: set[str] = set()

    for part in _SEARCH_PART_PATTERN.findall(text):
        if part.isascii():
            candidates = [part.lower()]
        elif len(part) == 1:
            candidates = [part]
        else:
            candidates = [part[index : index + 2] for index in range(len(part) - 1)]

        for token in candidates:
            if token not in seen:
                seen.add(token)
                tokens.append(token)

    return " ".join(tokens)

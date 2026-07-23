import json
from collections.abc import Iterable, Mapping
from uuid import UUID

from app.ai.contracts import ConversationMessage
from app.rag.schemas import RetrievedChunk

SYSTEM_PROMPT = """你是企业知识库问答助手。只能依据给定上下文回答。
上下文没有答案时，明确说明未找到足够依据。
不得编造政策、数字、日期、文件名或页码。
引用只能使用上下文已有的编号，例如 [1]。默认使用中文回答。"""

_MAX_UTF8_CHARACTER = "\U0010ffff"


def serialized_chat_input_token_upper_bound(system_prompt: str, user_prompt: str) -> int:
    """UTF-8 字节数是聊天消息序列化后 Token 数的保守上界。"""
    serialized = json.dumps(
        {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return len(serialized.encode("utf-8"))


def estimate_rag_input_token_upper_bound(
    *,
    question: str,
    history: Iterable[ConversationMessage | Mapping[str, str]],
    top_k: int,
    chunk_size: int,
) -> int:
    """按检索上限、切片上限和数据库字符串字段上限估算回答输入上界。"""
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
        raise ValueError("top_k 必须是正整数")
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size <= 0:
        raise ValueError("chunk_size 必须是正整数")
    worst_chunks = [
        RetrievedChunk(
            chunk_id=UUID(int=index + 1),
            document_id=UUID(int=index + 1),
            file_name=_MAX_UTF8_CHARACTER * 255,
            content=_MAX_UTF8_CHARACTER * chunk_size,
            relevance_score=1.0,
            page_number=-2_147_483_648,
            sheet_name=_MAX_UTF8_CHARACTER * 100,
            row_start=-2_147_483_648,
            section_title=_MAX_UTF8_CHARACTER * 500,
        )
        for index in range(top_k)
    ]
    system_prompt, user_prompt = build_rag_prompt(question, worst_chunks)
    history_payload = [
        {
            "role": item["role"] if isinstance(item, Mapping) else item.role,
            "content": item["content"] if isinstance(item, Mapping) else item.content,
        }
        for item in history
    ]
    serialized_history = json.dumps(
        {"history": history_payload},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return serialized_chat_input_token_upper_bound(system_prompt, user_prompt) + len(
        serialized_history.encode("utf-8")
    )


def _source(chunk: RetrievedChunk) -> str:
    parts = [f"文件：{chunk.file_name}"]
    if chunk.page_number is not None:
        parts.append(f"页码：{chunk.page_number}")
    if chunk.sheet_name is not None:
        parts.append(f"工作表：{chunk.sheet_name}")
    if chunk.row_start is not None:
        parts.append(f"起始行：{chunk.row_start}")
    if chunk.section_title is not None:
        parts.append(f"章节：{chunk.section_title}")
    return "；".join(parts)


def build_rag_prompt(question: str, chunks: list[RetrievedChunk]) -> tuple[str, str]:
    contexts = [
        f"[{index}] {_source(chunk)}\n{chunk.content}"
        for index, chunk in enumerate(chunks, start=1)
    ]
    user_prompt = f"上下文：\n\n{'\n\n'.join(contexts)}\n\n问题：{question}"
    return SYSTEM_PROMPT, user_prompt

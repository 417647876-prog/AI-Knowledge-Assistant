from app.rag.schemas import RetrievedChunk

SYSTEM_PROMPT = """你是企业知识库问答助手。只能依据给定上下文回答。
上下文没有答案时，明确说明未找到足够依据。
不得编造政策、数字、日期、文件名或页码。
引用只能使用上下文已有的编号，例如 [1]。默认使用中文回答。"""


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


def build_rag_prompt(
    question: str, chunks: list[RetrievedChunk]
) -> tuple[str, str]:
    contexts = [
        f"[{index}] {_source(chunk)}\n{chunk.content}"
        for index, chunk in enumerate(chunks, start=1)
    ]
    user_prompt = f"上下文：\n\n{'\n\n'.join(contexts)}\n\n问题：{question}"
    return SYSTEM_PROMPT, user_prompt

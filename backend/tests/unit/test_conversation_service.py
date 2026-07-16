from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.ai.contracts import ConversationMessage as PromptMessage
from app.conversations.service import StreamPersistenceState, build_completed_history
from app.db.models import ConversationMessage
from app.rag.streaming import StreamEvent


def message(sequence: int, role: str, content: str, status: str) -> ConversationMessage:
    now = datetime.now(UTC)
    return ConversationMessage(
        id=uuid4(),
        conversation_id=uuid4(),
        sequence_number=sequence,
        role=role,
        content=content,
        status=status,
        completed_at=now if status != "streaming" else None,
    )


def test_history_keeps_only_last_six_completed_question_answer_pairs() -> None:
    messages = [
        item
        for pair in range(1, 9)
        for item in (
            message(pair * 2 - 1, "user", f"问{pair}", "completed"),
            message(pair * 2, "assistant", f"答{pair}", "completed"),
        )
    ]

    history = build_completed_history(messages)

    assert history == [
        item
        for pair in range(3, 9)
        for item in (
            PromptMessage(role="user", content=f"问{pair}"),
            PromptMessage(role="assistant", content=f"答{pair}"),
        )
    ]


def test_history_ignores_incomplete_or_unpaired_answers() -> None:
    messages = [
        message(1, "user", "已完成问题", "completed"),
        message(2, "assistant", "已完成回答", "completed"),
        message(3, "user", "失败问题", "completed"),
        message(4, "assistant", "部分回答", "interrupted"),
        message(5, "user", "孤立问题", "completed"),
    ]

    assert build_completed_history(messages) == [
        PromptMessage(role="user", content="已完成问题"),
        PromptMessage(role="assistant", content="已完成回答"),
    ]


def test_history_uses_latest_completed_retry_for_each_user_question() -> None:
    messages = [
        message(1, "user", "问题", "completed"),
        message(2, "assistant", "旧回答", "completed"),
        message(3, "assistant", "重试后的回答", "completed"),
        message(4, "user", "下一问", "completed"),
        message(5, "assistant", "下一答", "completed"),
    ]

    assert build_completed_history(messages) == [
        PromptMessage(role="user", content="问题"),
        PromptMessage(role="assistant", content="重试后的回答"),
        PromptMessage(role="user", content="下一问"),
        PromptMessage(role="assistant", content="下一答"),
    ]


def test_history_assigns_retry_chain_to_original_question_without_stealing_later_answer() -> None:
    user_1 = message(1, "user", "问题1", "completed")
    answer_1 = message(2, "assistant", "回答1", "completed")
    user_2 = message(3, "user", "问题2", "completed")
    answer_2 = message(4, "assistant", "回答2", "completed")
    retry_1 = message(5, "assistant", "问题1重试1", "completed")
    retry_1.retry_of_message_id = answer_1.id
    retry_2 = message(6, "assistant", "问题1重试2", "completed")
    retry_2.retry_of_message_id = retry_1.id

    assert build_completed_history([user_1, answer_1, user_2, answer_2, retry_1, retry_2]) == [
        PromptMessage(role="user", content="问题1"),
        PromptMessage(role="assistant", content="问题1重试2"),
        PromptMessage(role="user", content="问题2"),
        PromptMessage(role="assistant", content="回答2"),
    ]


@pytest.mark.parametrize(
    ("role", "content"),
    [("user", "问" * 2001), ("assistant", "答" * 8001)],
    ids=("user-too-long", "assistant-too-long"),
)
def test_history_rejects_oversized_persisted_content(role: str, content: str) -> None:
    pair = [
        message(1, "user", content if role == "user" else "问题", "completed"),
        message(2, "assistant", content if role == "assistant" else "回答", "completed"),
    ]

    with pytest.raises(ValueError, match="历史消息内容长度"):
        build_completed_history(pair)


def test_stream_state_uses_explicit_rewrite_and_refusal_metadata() -> None:
    state = StreamPersistenceState()

    state.observe(
        StreamEvent(
            "rewrite",
            {"standalone_question": "问题", "used_fallback": False},
            persistence={"was_rewritten": False, "rewrite_fallback": False},
        )
    )
    state.observe(
        StreamEvent(
            "done",
            {"citations": [], "retrieved_chunk_count": 0, "timings": {}},
            persistence={"refused": True},
        )
    )

    assert state.was_rewritten is False
    assert state.rewrite_fallback is False
    assert state.refused is True


def test_stream_state_rejects_over_limit_token_before_mutating_partial_content() -> None:
    state = StreamPersistenceState(content="答" * 8000)

    with pytest.raises(Exception) as captured:
        state.observe(StreamEvent("token", {"delta": "超"}))

    assert getattr(captured.value, "code", None) == "ANSWER_TOO_LONG"
    assert state.content == "答" * 8000

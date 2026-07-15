from datetime import UTC, datetime, timedelta

import pytest

from app.jobs.service import (
    failure_transition,
    is_retryable_error,
    sanitize_failure,
    should_retry_failure,
)


@pytest.mark.parametrize(
    "error_code",
    ["TEMPORARY_NETWORK_ERROR", "MODEL_SERVICE_UNAVAILABLE", "MODEL_TIMEOUT"],
)
def test_transient_failures_are_retryable(error_code: str) -> None:
    assert is_retryable_error(error_code) is True


@pytest.mark.parametrize(
    "error_code",
    [
        "UNSUPPORTED_FILE_TYPE",
        "DOCUMENT_CORRUPTED",
        "DOCUMENT_CONTENT_EMPTY",
        "DOCUMENT_VALIDATION_FAILED",
    ],
)
def test_permanent_failures_are_not_retryable(error_code: str) -> None:
    assert is_retryable_error(error_code) is False


def test_first_retryable_failure_waits_30_seconds() -> None:
    now = datetime(2026, 7, 16, 9, 0, tzinfo=UTC)

    transition = failure_transition(
        attempt_number=1,
        max_attempts=3,
        retryable=True,
        now=now,
    )

    assert transition.status == "retry_wait"
    assert transition.run_after == now + timedelta(seconds=30)


def test_second_retryable_failure_waits_120_seconds() -> None:
    now = datetime(2026, 7, 16, 9, 0, tzinfo=UTC)

    transition = failure_transition(
        attempt_number=2,
        max_attempts=3,
        retryable=True,
        now=now,
    )

    assert transition.status == "retry_wait"
    assert transition.run_after == now + timedelta(seconds=120)


@pytest.mark.parametrize(
    ("attempt_number", "retryable"),
    [(3, True), (1, False)],
)
def test_exhausted_or_permanent_failure_is_terminal(attempt_number: int, retryable: bool) -> None:
    now = datetime(2026, 7, 16, 9, 0, tzinfo=UTC)

    transition = failure_transition(
        attempt_number=attempt_number,
        max_attempts=3,
        retryable=retryable,
        now=now,
    )

    assert transition.status == "failed"
    assert transition.run_after == now


def test_failure_message_removes_paths_connection_strings_and_response_bodies() -> None:
    code, message = sanitize_failure(
        "MODEL_TIMEOUT",
        "C:\\secret\\document.pdf 调用 postgresql://user:pass@db/app 失败，response body={bad}",
    )

    assert code == "MODEL_TIMEOUT"
    assert message == "任务处理暂时失败，请稍后重试。"
    assert "secret" not in message
    assert "postgresql" not in message
    assert "response body" not in message


def test_unstable_failure_code_is_replaced() -> None:
    code, message = sanitize_failure("ValueError: /tmp/private", "raw exception")

    assert code == "JOB_PROCESSING_ERROR"
    assert message == "任务处理失败。"


def test_unknown_stable_code_and_third_party_body_are_replaced() -> None:
    code, message = sanitize_failure(
        "THIRD_PARTY_REJECTED",
        "api_key=secret; upstream returned confidential payload",
    )

    assert code == "JOB_PROCESSING_ERROR"
    assert message == "任务处理失败。"


def test_known_code_uses_canonical_message_instead_of_caller_text() -> None:
    code, message = sanitize_failure("DOCUMENT_CONTENT_EMPTY", "api_key=secret")

    assert code == "DOCUMENT_CONTENT_EMPTY"
    assert message == "文档没有可入库的内容。"


@pytest.mark.parametrize(
    "error_code",
    [
        "DOCUMENT_CORRUPTED",
        "UNSUPPORTED_FILE_TYPE",
        "DOCUMENT_VALIDATION_FAILED",
        "UNKNOWN_PROVIDER_FAILURE",
    ],
)
def test_retry_request_cannot_override_permanent_or_unknown_error_policy(
    error_code: str,
) -> None:
    assert should_retry_failure(error_code, requested=True) is False


def test_known_transient_error_retries_only_when_caller_allows_it() -> None:
    assert should_retry_failure("MODEL_TIMEOUT", requested=True) is True
    assert should_retry_failure("MODEL_TIMEOUT", requested=False) is False

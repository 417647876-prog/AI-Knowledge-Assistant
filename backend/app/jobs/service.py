from dataclasses import dataclass
from datetime import datetime, timedelta

from app.jobs.contracts import JobStatus

RETRY_BACKOFF_SECONDS = (30, 120)
RETRYABLE_ERROR_CODES = frozenset(
    {
        "TEMPORARY_NETWORK_ERROR",
        "MODEL_SERVICE_UNAVAILABLE",
        "MODEL_TIMEOUT",
    }
)
_CANONICAL_FAILURE_MESSAGES = {
    "TEMPORARY_NETWORK_ERROR": "任务处理暂时失败，请稍后重试。",
    "MODEL_SERVICE_UNAVAILABLE": "任务处理暂时失败，请稍后重试。",
    "MODEL_TIMEOUT": "任务处理暂时失败，请稍后重试。",
    "UNSUPPORTED_FILE_TYPE": "当前不支持该文件格式。",
    "DOCUMENT_CORRUPTED": "文档已损坏，无法解析。",
    "DOCUMENT_CONTENT_EMPTY": "文档没有可入库的内容。",
    "DOCUMENT_VALIDATION_FAILED": "文档校验失败。",
    "JOB_HANDLER_UNAVAILABLE": "当前任务暂不可处理。",
    "JOB_PROCESSING_ERROR": "任务处理失败。",
}


@dataclass(frozen=True, slots=True)
class FailureTransition:
    status: JobStatus
    run_after: datetime


def is_retryable_error(error_code: str) -> bool:
    return error_code in RETRYABLE_ERROR_CODES


def should_retry_failure(error_code: str, *, requested: bool) -> bool:
    return requested and is_retryable_error(error_code)


def failure_transition(
    *,
    attempt_number: int,
    max_attempts: int,
    retryable: bool,
    now: datetime,
    backoff_seconds: tuple[int, ...] = RETRY_BACKOFF_SECONDS,
) -> FailureTransition:
    if retryable and attempt_number < max_attempts:
        backoff_index = min(attempt_number - 1, len(backoff_seconds) - 1)
        return FailureTransition(
            status="retry_wait",
            run_after=now + timedelta(seconds=backoff_seconds[backoff_index]),
        )
    return FailureTransition(status="failed", run_after=now)


def sanitize_failure(code: str, _message: str) -> tuple[str, str]:
    canonical_message = _CANONICAL_FAILURE_MESSAGES.get(code)
    if canonical_message is None:
        return "JOB_PROCESSING_ERROR", "任务处理失败。"
    return code, canonical_message

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.core.exceptions import AppError
from app.jobs.contracts import JobLease
from app.jobs.repository import LeaseLostError
from app.worker import health as worker_health
from app.worker import main as worker_main


def _lease() -> JobLease:
    now = datetime.now(UTC)
    return JobLease(
        job_id=uuid4(),
        job_type="ingest_document",
        resource_type="document",
        resource_id=uuid4(),
        owner_user_id=uuid4(),
        knowledge_base_id=uuid4(),
        attempt_number=1,
        lease_token=uuid4(),
        lease_expires_at=now + timedelta(seconds=120),
    )


class _Session:
    def __init__(self) -> None:
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class _SessionFactory:
    def __init__(self) -> None:
        self.sessions: list[_Session] = []

    def __call__(self) -> _Session:
        session = _Session()
        self.sessions.append(session)
        return session


@pytest.mark.asyncio
async def test_worker_iteration_claims_at_most_one_job(monkeypatch: pytest.MonkeyPatch) -> None:
    lease = _lease()
    claim = AsyncMock(return_value=lease)
    complete = AsyncMock(return_value=True)
    process = AsyncMock(return_value=7)
    monkeypatch.setattr(worker_main, "claim_next_job", claim)
    monkeypatch.setattr(worker_main, "complete_job", complete)
    monkeypatch.setattr(worker_main, "renew_lease", AsyncMock(return_value=True))
    monkeypatch.setattr(worker_main, "record_worker_heartbeat", AsyncMock())

    processed = await worker_main.run_worker_iteration(
        session_factory=_SessionFactory(),
        settings=Settings(_env_file=None),
        worker_id="worker-a",
        process_job=process,
    )

    assert processed is True
    claim.assert_awaited_once()
    process.assert_awaited_once_with(lease)
    complete.assert_awaited_once()
    assert complete.await_args.kwargs["lease_token"] == lease.lease_token


@pytest.mark.asyncio
async def test_worker_completes_purge_when_handler_returns_plain_chunk_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _lease()
    lease = JobLease(
        job_id=original.job_id,
        job_type="purge_document",
        resource_type="document",
        resource_id=original.resource_id,
        owner_user_id=original.owner_user_id,
        knowledge_base_id=original.knowledge_base_id,
        attempt_number=original.attempt_number,
        lease_token=original.lease_token,
        lease_expires_at=original.lease_expires_at,
    )
    complete = AsyncMock(return_value=True)
    fail = AsyncMock()
    monkeypatch.setattr(worker_main, "claim_next_job", AsyncMock(return_value=lease))
    monkeypatch.setattr(worker_main, "complete_job", complete)
    monkeypatch.setattr(worker_main, "fail_job", fail)
    monkeypatch.setattr(worker_main, "renew_lease", AsyncMock(return_value=True))
    monkeypatch.setattr(worker_main, "record_worker_heartbeat", AsyncMock())

    processed = await worker_main.run_worker_iteration(
        session_factory=_SessionFactory(),
        settings=Settings(_env_file=None),
        worker_id="worker-a",
        process_job=AsyncMock(return_value=0),
    )

    assert processed is True
    complete.assert_awaited_once()
    fail.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_skips_complete_only_for_explicit_handler_finalized_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _lease()
    lease = JobLease(
        job_id=original.job_id,
        job_type="purge_document",
        resource_type="document",
        resource_id=original.resource_id,
        owner_user_id=original.owner_user_id,
        knowledge_base_id=original.knowledge_base_id,
        attempt_number=original.attempt_number,
        lease_token=original.lease_token,
        lease_expires_at=original.lease_expires_at,
    )
    complete = AsyncMock()
    fail = AsyncMock()
    monkeypatch.setattr(worker_main, "claim_next_job", AsyncMock(return_value=lease))
    monkeypatch.setattr(worker_main, "complete_job", complete)
    monkeypatch.setattr(worker_main, "fail_job", fail)
    monkeypatch.setattr(worker_main, "renew_lease", AsyncMock(return_value=True))
    monkeypatch.setattr(worker_main, "record_worker_heartbeat", AsyncMock())

    result = worker_main.ProcessResult(
        chunk_count=0,
        completion_mode=worker_main.HANDLER_FINALIZED,
    )
    processed = await worker_main.run_worker_iteration(
        session_factory=_SessionFactory(),
        settings=Settings(_env_file=None),
        worker_id="worker-a",
        process_job=AsyncMock(return_value=result),
    )

    assert processed is True
    complete.assert_not_awaited()
    fail.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_iteration_does_not_call_processor(monkeypatch: pytest.MonkeyPatch) -> None:
    process = AsyncMock()
    monkeypatch.setattr(worker_main, "claim_next_job", AsyncMock(return_value=None))
    monkeypatch.setattr(worker_main, "record_worker_heartbeat", AsyncMock())

    processed = await worker_main.run_worker_iteration(
        session_factory=_SessionFactory(),
        settings=Settings(_env_file=None),
        worker_id="worker-a",
        process_job=process,
    )

    assert processed is False
    process.assert_not_awaited()


@pytest.mark.asyncio
async def test_heartbeat_renews_lease_in_an_independent_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _lease()
    stop = asyncio.Event()
    renew = AsyncMock(return_value=True)
    record = AsyncMock()
    monkeypatch.setattr(worker_main, "renew_lease", renew)
    monkeypatch.setattr(worker_main, "record_worker_heartbeat", record)

    async def wait_once(_stop: asyncio.Event, _seconds: float) -> bool:
        stop.set()
        return False

    factory = _SessionFactory()
    healthy = await worker_main.heartbeat_lease(
        session_factory=factory,
        lease=lease,
        worker_id="worker-a",
        lease_seconds=120,
        heartbeat_seconds=15,
        stop_event=stop,
        wait_for_stop=wait_once,
    )

    assert len(factory.sessions) == 1
    renew.assert_awaited_once()
    record.assert_awaited_once()
    factory.sessions[0].commit.assert_awaited_once()
    assert healthy is True


@pytest.mark.asyncio
async def test_heartbeat_database_error_marks_lease_lost_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _lease()
    stop = asyncio.Event()
    monkeypatch.setattr(
        worker_main,
        "renew_lease",
        AsyncMock(side_effect=SQLAlchemyError("postgresql://secret")),
    )

    async def run_now(_stop: asyncio.Event, _seconds: float) -> bool:
        return False

    healthy = await worker_main.heartbeat_lease(
        session_factory=_SessionFactory(),
        lease=lease,
        worker_id="worker-a",
        lease_seconds=120,
        heartbeat_seconds=15,
        stop_event=stop,
        wait_for_stop=run_now,
    )

    assert healthy is False


@pytest.mark.asyncio
async def test_worker_cancels_processor_when_heartbeat_loses_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _lease()
    canceled = asyncio.Event()
    monkeypatch.setattr(worker_main, "claim_next_job", AsyncMock(return_value=lease))
    monkeypatch.setattr(worker_main, "heartbeat_lease", AsyncMock(return_value=False))
    complete = AsyncMock()
    fail = AsyncMock()
    monkeypatch.setattr(worker_main, "complete_job", complete)
    monkeypatch.setattr(worker_main, "fail_job", fail)
    monkeypatch.setattr(worker_main, "record_worker_heartbeat", AsyncMock())

    async def long_process(_lease: JobLease) -> int:
        try:
            await asyncio.Event().wait()
        finally:
            canceled.set()
        return 0

    processed = await asyncio.wait_for(
        worker_main.run_worker_iteration(
            session_factory=_SessionFactory(),
            settings=Settings(_env_file=None),
            worker_id="worker-a",
            process_job=long_process,
        ),
        timeout=0.2,
    )

    assert processed is True
    assert canceled.is_set()
    complete.assert_not_awaited()
    fail.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_iteration_treats_processor_lease_loss_as_completed_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _lease()
    complete = AsyncMock()
    fail = AsyncMock()
    monkeypatch.setattr(worker_main, "claim_next_job", AsyncMock(return_value=lease))
    monkeypatch.setattr(worker_main, "complete_job", complete)
    monkeypatch.setattr(worker_main, "fail_job", fail)
    monkeypatch.setattr(worker_main, "renew_lease", AsyncMock(return_value=True))
    monkeypatch.setattr(worker_main, "record_worker_heartbeat", AsyncMock())

    async def loses_lease(_lease: JobLease) -> int:
        raise LeaseLostError("late result")

    processed = await worker_main.run_worker_iteration(
        session_factory=_SessionFactory(),
        settings=Settings(_env_file=None),
        worker_id="worker-a",
        process_job=loses_lease,
    )

    assert processed is True
    complete.assert_not_awaited()
    fail.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_loop_continues_after_processor_lease_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _lease()
    stop = asyncio.Event()
    attempts = 0
    monkeypatch.setattr(worker_main, "claim_next_job", AsyncMock(return_value=lease))
    monkeypatch.setattr(worker_main, "complete_job", AsyncMock(return_value=True))
    monkeypatch.setattr(worker_main, "fail_job", AsyncMock())
    monkeypatch.setattr(worker_main, "renew_lease", AsyncMock(return_value=True))
    monkeypatch.setattr(worker_main, "record_worker_heartbeat", AsyncMock())

    async def process(_lease: JobLease) -> int:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise LeaseLostError("reclaimed")
        stop.set()
        return 1

    await worker_main.run_worker(
        session_factory=_SessionFactory(),
        settings=Settings(_env_file=None),
        worker_id="worker-a",
        process_job=process,
        stop_event=stop,
    )

    assert attempts == 2


@pytest.mark.asyncio
async def test_worker_records_only_sanitized_generic_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _lease()
    fail = AsyncMock(return_value="retry_wait")
    monkeypatch.setattr(worker_main, "claim_next_job", AsyncMock(return_value=lease))
    monkeypatch.setattr(worker_main, "fail_job", fail)
    monkeypatch.setattr(worker_main, "renew_lease", AsyncMock(return_value=True))
    monkeypatch.setattr(worker_main, "record_worker_heartbeat", AsyncMock())

    async def raises_secret(_lease: JobLease) -> int:
        raise RuntimeError("postgresql://user:pass@db/private response body=secret")

    processed = await worker_main.run_worker_iteration(
        session_factory=_SessionFactory(),
        settings=Settings(_env_file=None),
        worker_id="worker-a",
        process_job=raises_secret,
    )

    assert processed is True
    assert fail.await_args.kwargs["code"] == "JOB_PROCESSING_ERROR"
    assert fail.await_args.kwargs["message"] == "任务处理失败。"
    assert fail.await_args.kwargs["retryable"] is False


@pytest.mark.parametrize(
    ("raised_code", "repository_code"),
    [
        ("DOCUMENT_CORRUPTED", "DOCUMENT_CORRUPTED"),
        ("UNKNOWN_PROVIDER_FAILURE", "JOB_PROCESSING_ERROR"),
    ],
)
@pytest.mark.asyncio
async def test_worker_routes_retry_requested_permanent_error_through_repository_policy(
    monkeypatch: pytest.MonkeyPatch,
    raised_code: str,
    repository_code: str,
) -> None:
    lease = _lease()
    fail = AsyncMock(return_value="failed")
    monkeypatch.setattr(worker_main, "claim_next_job", AsyncMock(return_value=lease))
    monkeypatch.setattr(worker_main, "fail_job", fail)
    monkeypatch.setattr(worker_main, "renew_lease", AsyncMock(return_value=True))
    monkeypatch.setattr(worker_main, "record_worker_heartbeat", AsyncMock())

    async def raises_permanent(_lease: JobLease) -> int:
        raise worker_main.JobExecutionError(
            code=raised_code,
            message="不应被持久化的第三方正文",
            retryable=True,
        )

    await worker_main.run_worker_iteration(
        session_factory=_SessionFactory(),
        settings=Settings(_env_file=None),
        worker_id="worker-a",
        process_job=raises_permanent,
    )

    fail.assert_awaited_once()
    assert fail.await_args.kwargs["code"] == repository_code
    assert fail.await_args.kwargs["retryable"] is True


@pytest.mark.asyncio
async def test_worker_passes_configured_retry_backoff_to_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _lease()
    fail = AsyncMock(return_value="retry_wait")
    monkeypatch.setattr(worker_main, "claim_next_job", AsyncMock(return_value=lease))
    monkeypatch.setattr(worker_main, "fail_job", fail)
    monkeypatch.setattr(worker_main, "renew_lease", AsyncMock(return_value=True))
    monkeypatch.setattr(worker_main, "record_worker_heartbeat", AsyncMock())

    async def raises_timeout(_lease: JobLease) -> int:
        raise worker_main.JobExecutionError(
            code="MODEL_TIMEOUT",
            message="上游超时",
            retryable=True,
        )

    await worker_main.run_worker_iteration(
        session_factory=_SessionFactory(),
        settings=Settings(_env_file=None, job_retry_backoff_seconds=(7, 19)),
        worker_id="worker-a",
        process_job=raises_timeout,
    )

    assert fail.await_args.kwargs["retry_backoff_seconds"] == (7, 19)


@pytest.mark.asyncio
async def test_worker_retries_transient_embedding_app_error_with_configured_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _lease()
    fail = AsyncMock(return_value="retry_wait")
    monkeypatch.setattr(worker_main, "claim_next_job", AsyncMock(return_value=lease))
    monkeypatch.setattr(worker_main, "fail_job", fail)
    monkeypatch.setattr(worker_main, "renew_lease", AsyncMock(return_value=True))
    monkeypatch.setattr(worker_main, "record_worker_heartbeat", AsyncMock())

    async def timeout(_lease: JobLease) -> int:
        raise AppError(code="MODEL_TIMEOUT", message="upstream timeout", status_code=502)

    await worker_main.run_worker_iteration(
        session_factory=_SessionFactory(),
        settings=Settings(_env_file=None, job_retry_backoff_seconds=(7, 19)),
        worker_id="worker-a",
        process_job=timeout,
    )

    assert fail.await_args.kwargs["code"] == "MODEL_TIMEOUT"
    assert fail.await_args.kwargs["retryable"] is True
    assert fail.await_args.kwargs["retry_backoff_seconds"] == (7, 19)


@pytest.mark.asyncio
async def test_worker_discards_failure_transition_when_lease_is_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _lease()
    complete = AsyncMock()
    fail = AsyncMock(side_effect=LeaseLostError("reclaimed before fail transition"))
    monkeypatch.setattr(worker_main, "claim_next_job", AsyncMock(return_value=lease))
    monkeypatch.setattr(worker_main, "complete_job", complete)
    monkeypatch.setattr(worker_main, "fail_job", fail)
    monkeypatch.setattr(worker_main, "renew_lease", AsyncMock(return_value=True))
    monkeypatch.setattr(worker_main, "record_worker_heartbeat", AsyncMock())

    async def raises_app_error(_lease: JobLease) -> int:
        raise AppError(code="MODEL_TIMEOUT", message="timeout", status_code=502)

    processed = await worker_main.run_worker_iteration(
        session_factory=_SessionFactory(),
        settings=Settings(_env_file=None),
        worker_id="worker-a",
        process_job=raises_app_error,
    )

    assert processed is True
    fail.assert_awaited_once()
    complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_loop_continues_after_failure_transition_loses_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _lease()
    stop = asyncio.Event()
    attempts = 0
    complete = AsyncMock(return_value=True)
    fail = AsyncMock(side_effect=LeaseLostError("reclaimed before fail transition"))
    monkeypatch.setattr(worker_main, "claim_next_job", AsyncMock(return_value=lease))
    monkeypatch.setattr(worker_main, "complete_job", complete)
    monkeypatch.setattr(worker_main, "fail_job", fail)
    monkeypatch.setattr(worker_main, "renew_lease", AsyncMock(return_value=True))
    monkeypatch.setattr(worker_main, "record_worker_heartbeat", AsyncMock())

    async def process(_lease: JobLease) -> int:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise AppError(code="MODEL_TIMEOUT", message="timeout", status_code=502)
        stop.set()
        return 1

    await worker_main.run_worker(
        session_factory=_SessionFactory(),
        settings=Settings(_env_file=None),
        worker_id="worker-a",
        process_job=process,
        stop_event=stop,
    )

    assert attempts == 2
    fail.assert_awaited_once()
    complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_empty_worker_loop_waits_poll_interval_and_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = asyncio.Event()
    monkeypatch.setattr(worker_main, "run_worker_iteration", AsyncMock(return_value=False))
    waits: list[float] = []

    async def stop_after_wait(_stop: asyncio.Event, seconds: float) -> bool:
        waits.append(seconds)
        stop.set()
        return True

    await worker_main.run_worker(
        session_factory=_SessionFactory(),
        settings=Settings(_env_file=None),
        worker_id="worker-a",
        process_job=AsyncMock(),
        stop_event=stop,
        wait_for_stop=stop_after_wait,
    )

    assert waits == [2]


@pytest.mark.asyncio
async def test_health_check_uses_own_worker_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fresh = AsyncMock(return_value=True)
    monkeypatch.setattr(
        worker_health,
        "get_settings",
        lambda: Settings(_env_file=None, worker_id="worker-a"),
    )
    monkeypatch.setattr(worker_health, "session_factory", _SessionFactory())
    monkeypatch.setattr(worker_health, "worker_heartbeat_is_fresh", fresh)

    healthy = await worker_health.check_health(max_age_seconds=60)

    assert healthy is True
    assert fresh.await_args.kwargs["worker_id"] == "worker-a"
    assert fresh.await_args.kwargs["max_age_seconds"] == 60


@pytest.mark.asyncio
async def test_health_check_returns_false_when_database_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenFactory:
        def __call__(self) -> _Session:
            raise worker_health.SQLAlchemyError("database unavailable")

    monkeypatch.setattr(worker_health, "session_factory", BrokenFactory())

    assert await worker_health.check_health(max_age_seconds=60) is False


@pytest.mark.asyncio
async def test_default_processor_routes_ingestion_to_registered_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _lease()
    handler = AsyncMock(return_value=4)
    monkeypatch.setattr(worker_main, "process_ingest_document", handler)

    assert await worker_main.process_job(lease) == 4
    handler.assert_awaited_once()
    assert handler.await_args.args[0] == lease


@pytest.mark.asyncio
async def test_default_processor_routes_purge_to_registered_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _lease()
    purge_lease = JobLease(
        job_id=lease.job_id,
        job_type="purge_document",
        resource_type=lease.resource_type,
        resource_id=lease.resource_id,
        owner_user_id=lease.owner_user_id,
        knowledge_base_id=lease.knowledge_base_id,
        attempt_number=lease.attempt_number,
        lease_token=lease.lease_token,
        lease_expires_at=lease.lease_expires_at,
    )
    handler = AsyncMock(return_value=0)
    monkeypatch.setattr(worker_main, "purge_document", handler)

    assert await worker_main.process_job(purge_lease) == 0
    handler.assert_awaited_once()
    assert handler.await_args.kwargs["lease"] == purge_lease


@pytest.mark.asyncio
async def test_default_processor_fails_closed_for_unregistered_job_type() -> None:
    lease = _lease()
    unsupported_lease = JobLease(
        job_id=lease.job_id,
        job_type="export_document",  # type: ignore[arg-type]
        resource_type=lease.resource_type,
        resource_id=lease.resource_id,
        owner_user_id=lease.owner_user_id,
        knowledge_base_id=lease.knowledge_base_id,
        attempt_number=lease.attempt_number,
        lease_token=lease.lease_token,
        lease_expires_at=lease.lease_expires_at,
    )
    with pytest.raises(worker_main.JobExecutionError) as captured:
        await worker_main.process_job(unsupported_lease)

    assert captured.value.code == "JOB_HANDLER_UNAVAILABLE"
    assert captured.value.retryable is False


def test_health_cli_returns_one_for_stale_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["app.worker.health", "--max-age-seconds", "60"])
    monkeypatch.setattr(worker_health, "check_health", AsyncMock(return_value=False))

    assert worker_health.main() == 1

import argparse
import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.db.session import session_factory as default_session_factory
from app.jobs.contracts import (
    HANDLER_FINALIZED,
    WORKER_COMPLETES,
    JobLease,
    ProcessResult,
)
from app.jobs.repository import (
    LeaseLostError,
    claim_next_job,
    complete_job,
    fail_job,
    record_worker_heartbeat,
    renew_lease,
)
from app.jobs.service import is_retryable_error, sanitize_failure
from app.knowledge.background import process_ingest_document
from app.lifecycle.service import purge_document, purge_knowledge_base

logger = logging.getLogger(__name__)


class SessionFactory(Protocol):
    def __call__(self) -> AsyncSession: ...


ProcessJob = Callable[[JobLease], Awaitable[int | ProcessResult]]
WaitForStop = Callable[[asyncio.Event, float], Awaitable[bool]]


class JobExecutionError(RuntimeError):
    def __init__(self, *, code: str, message: str, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.user_message = message
        self.retryable = retryable


async def _wait_for_stop(stop_event: asyncio.Event, seconds: float) -> bool:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except TimeoutError:
        return False
    return True


async def heartbeat_lease(
    *,
    session_factory: SessionFactory,
    lease: JobLease,
    worker_id: str,
    lease_seconds: int,
    heartbeat_seconds: float,
    stop_event: asyncio.Event,
    wait_for_stop: WaitForStop = _wait_for_stop,
) -> bool:
    while not stop_event.is_set():
        if await wait_for_stop(stop_event, heartbeat_seconds):
            break
        now = datetime.now(UTC)
        try:
            async with session_factory() as session:
                renewed = await renew_lease(
                    session,
                    job_id=lease.job_id,
                    lease_token=lease.lease_token,
                    now=now,
                    lease_seconds=lease_seconds,
                )
                if renewed:
                    await record_worker_heartbeat(
                        session,
                        worker_id=worker_id,
                        status="processing",
                        current_job_id=lease.job_id,
                        now=now,
                    )
                await session.commit()
        except SQLAlchemyError as error:
            logger.error(
                "Worker 心跳数据库操作失败",
                extra={"job_id": str(lease.job_id), "error_type": type(error).__name__},
            )
            return False
        if not renewed:
            return False
    return True


async def run_worker_iteration(
    *,
    session_factory: SessionFactory,
    settings: Settings,
    worker_id: str,
    process_job: ProcessJob,
) -> bool:
    now = datetime.now(UTC)
    async with session_factory() as session:
        lease = await claim_next_job(
            session,
            worker_id=worker_id,
            now=now,
            lease_seconds=settings.job_lease_seconds,
        )
        await record_worker_heartbeat(
            session,
            worker_id=worker_id,
            status="processing" if lease else "idle",
            current_job_id=lease.job_id if lease else None,
            now=now,
        )
        await session.commit()

    if lease is None:
        return False

    heartbeat_stop = asyncio.Event()
    heartbeat = asyncio.create_task(
        heartbeat_lease(
            session_factory=session_factory,
            lease=lease,
            worker_id=worker_id,
            lease_seconds=settings.job_lease_seconds,
            heartbeat_seconds=settings.worker_heartbeat_seconds,
            stop_event=heartbeat_stop,
        )
    )
    processor = asyncio.create_task(process_job(lease))
    try:
        done, _pending = await asyncio.wait(
            {heartbeat, processor}, return_when=asyncio.FIRST_COMPLETED
        )
        if heartbeat in done and not await heartbeat:
            logger.warning("Worker 租约已失效", extra={"job_id": str(lease.job_id)})
            processor.cancel()
            await asyncio.gather(processor, return_exceptions=True)
            return True

        raw_result = await processor
        result = (
            raw_result
            if isinstance(raw_result, ProcessResult)
            else ProcessResult(
                chunk_count=raw_result,
                completion_mode=WORKER_COMPLETES,
            )
        )
        if result.completion_mode != HANDLER_FINALIZED:
            async with session_factory() as session:
                completed = await complete_job(
                    session,
                    job_id=lease.job_id,
                    lease_token=lease.lease_token,
                    chunk_count=result.chunk_count,
                    now=datetime.now(UTC),
                )
                await session.commit()
            if not completed:
                logger.warning("Worker 完成任务时租约已失效", extra={"job_id": str(lease.job_id)})
    except asyncio.CancelledError:
        raise
    except LeaseLostError:
        logger.warning("Worker 处理结果因租约失效被丢弃", extra={"job_id": str(lease.job_id)})
        return True
    except SQLAlchemyError:
        raise
    except Exception as error:
        if isinstance(error, JobExecutionError):
            code, message = sanitize_failure(error.code, error.user_message)
            retryable = error.retryable
        elif isinstance(error, AppError):
            code, message = sanitize_failure(error.code, error.message)
            retryable = is_retryable_error(code)
        else:
            code, message, retryable = "JOB_PROCESSING_ERROR", "任务处理失败。", False
        logger.error(
            "Worker 任务处理失败",
            extra={"job_id": str(lease.job_id), "error_type": type(error).__name__},
        )
        try:
            async with session_factory() as session:
                await fail_job(
                    session,
                    job_id=lease.job_id,
                    lease_token=lease.lease_token,
                    code=code,
                    message=message,
                    retryable=retryable,
                    now=datetime.now(UTC),
                    retry_backoff_seconds=settings.job_retry_backoff_seconds,
                )
                await session.commit()
        except LeaseLostError:
            logger.warning(
                "Worker 失败结果因租约失效被丢弃",
                extra={"job_id": str(lease.job_id)},
            )
            return True
    finally:
        heartbeat_stop.set()
        if not processor.done():
            processor.cancel()
            await asyncio.gather(processor, return_exceptions=True)
        heartbeat_result = await asyncio.gather(heartbeat, return_exceptions=True)
        if isinstance(heartbeat_result[0], BaseException):
            logger.error(
                "Worker 心跳异常退出",
                extra={
                    "job_id": str(lease.job_id),
                    "error_type": type(heartbeat_result[0]).__name__,
                },
            )
        async with session_factory() as session:
            await record_worker_heartbeat(
                session,
                worker_id=worker_id,
                status="idle",
                current_job_id=None,
                now=datetime.now(UTC),
            )
            await session.commit()
    return True


async def run_worker(
    *,
    session_factory: SessionFactory,
    settings: Settings,
    worker_id: str,
    process_job: ProcessJob,
    stop_event: asyncio.Event,
    wait_for_stop: WaitForStop = _wait_for_stop,
) -> None:
    while not stop_event.is_set():
        try:
            processed = await run_worker_iteration(
                session_factory=session_factory,
                settings=settings,
                worker_id=worker_id,
                process_job=process_job,
            )
        except SQLAlchemyError as error:
            logger.warning(
                "Worker 数据库暂时不可用，等待后重试",
                extra={"error_type": type(error).__name__},
            )
            await wait_for_stop(stop_event, settings.worker_poll_seconds)
            continue
        if not processed:
            await wait_for_stop(stop_event, settings.worker_poll_seconds)


async def process_job(lease: JobLease) -> int | ProcessResult:
    settings = get_settings()
    if lease.job_type == "ingest_document":
        return await process_ingest_document(lease, settings)
    if lease.job_type == "purge_document":
        return await purge_document(
            session_factory=default_session_factory,
            upload_directory=settings.upload_directory,
            lease=lease,
            retention_days=settings.trash_retention_days,
        )
    if lease.job_type == "purge_knowledge_base":
        return await purge_knowledge_base(
            session_factory=default_session_factory,
            upload_directory=settings.upload_directory,
            lease=lease,
            retention_days=settings.trash_retention_days,
        )
    raise JobExecutionError(
        code="JOB_HANDLER_UNAVAILABLE",
        message=f"任务类型 {lease.job_type} 暂不可处理。",
        retryable=False,
    )


async def _run() -> None:
    settings = get_settings()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    previous_handlers: dict[signal.Signals, object] = {}
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signal_number] = signal.getsignal(signal_number)
        signal.signal(
            signal_number,
            lambda *_args: loop.call_soon_threadsafe(stop_event.set),
        )
    try:
        await run_worker(
            session_factory=default_session_factory,
            settings=settings,
            worker_id=settings.worker_id,
            process_job=process_job,
            stop_event=stop_event,
        )
    finally:
        for signal_number, handler in previous_handlers.items():
            signal.signal(signal_number, handler)


def main() -> int:
    argparse.ArgumentParser(description="运行数据库任务 Worker").parse_args()
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

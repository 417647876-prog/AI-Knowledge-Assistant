from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

JobType = Literal[
    "ingest_document",
    "purge_document",
    "purge_knowledge_base",
]
JobStatus = Literal[
    "pending",
    "processing",
    "retry_wait",
    "succeeded",
    "failed",
    "canceled",
]
CompletionMode = Literal["worker_completes", "handler_finalized"]
WORKER_COMPLETES: CompletionMode = "worker_completes"
HANDLER_FINALIZED: CompletionMode = "handler_finalized"


@dataclass(frozen=True, slots=True)
class ProcessResult:
    chunk_count: int
    completion_mode: CompletionMode = WORKER_COMPLETES

    def __post_init__(self) -> None:
        if self.chunk_count < 0:
            raise ValueError("chunk_count 不能小于 0")
        if self.completion_mode not in (WORKER_COMPLETES, HANDLER_FINALIZED):
            raise ValueError("未知的任务完成模式")


@dataclass(frozen=True, slots=True)
class JobLease:
    job_id: UUID
    job_type: JobType
    resource_type: str
    resource_id: UUID
    owner_user_id: UUID
    knowledge_base_id: UUID
    attempt_number: int
    lease_token: UUID
    lease_expires_at: datetime

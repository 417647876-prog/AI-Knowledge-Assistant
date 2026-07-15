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

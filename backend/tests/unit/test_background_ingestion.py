from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.ai.embeddings import LocalEmbeddingProvider
from app.core.config import Settings
from app.jobs.contracts import JobLease
from app.knowledge import background


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


@pytest.mark.asyncio
async def test_worker_ingestion_uses_cached_local_embedding_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[JobLease, object]] = []
    settings = Settings(_env_file=None, embedding_provider="local")

    async def capture_provider(
        lease: JobLease, current_settings: Settings, provider: object
    ) -> int:
        assert current_settings is settings
        captured.append((lease, provider))
        return 3

    monkeypatch.setattr(background, "_process_with_provider", capture_provider)
    first_lease = _lease()
    second_lease = _lease()

    first_count = await background.process_ingest_document(first_lease, settings)
    second_count = await background.process_ingest_document(second_lease, settings)

    assert (first_count, second_count) == (3, 3)
    assert [item[0] for item in captured] == [first_lease, second_lease]
    assert isinstance(captured[0][1], LocalEmbeddingProvider)
    assert captured[0][1] is captured[1][1]


@pytest.mark.asyncio
async def test_worker_ingestion_rejects_wrong_job_type() -> None:
    lease = _lease()
    wrong_lease = JobLease(
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

    with pytest.raises(ValueError, match="ingest_document"):
        await background.process_ingest_document(wrong_lease, Settings(_env_file=None))

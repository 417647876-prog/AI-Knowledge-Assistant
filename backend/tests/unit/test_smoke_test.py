from collections.abc import Iterator

import httpx
import pytest

from scripts.smoke_test import SmokeTestError, wait_for_document_ready


def _responses(statuses: list[str]) -> httpx.MockTransport:
    values: Iterator[str] = iter(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": next(values)})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_wait_for_document_ready_returns_when_document_is_ready() -> None:
    async with httpx.AsyncClient(transport=_responses(["pending", "ready"])) as client:
        payload = await wait_for_document_ready(
            client, "http://testserver", "document-id", timeout_seconds=1, poll_interval_seconds=0
        )

    assert payload["status"] == "ready"


@pytest.mark.asyncio
async def test_wait_for_document_ready_raises_with_processing_error() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"status": "failed", "error_message": "无法解析文档"},
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(SmokeTestError, match="无法解析文档"):
            await wait_for_document_ready(
                client,
                "http://testserver",
                "document-id",
                timeout_seconds=1,
                poll_interval_seconds=0,
            )

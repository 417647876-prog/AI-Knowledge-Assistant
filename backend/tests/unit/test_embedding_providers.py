import httpx
import pytest

from app.ai.embeddings import (
    FakeEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    validate_embeddings,
)
from app.core.exceptions import AppError


@pytest.mark.asyncio
async def test_fake_embeddings_are_deterministic_and_1536_dimensions() -> None:
    provider = FakeEmbeddingProvider(dimensions=1536)

    first = await provider.embed_documents(["同一段文字"])
    second = await provider.embed_documents(["同一段文字"])

    assert first == second
    assert len(first[0]) == 1536


def test_validate_embeddings_rejects_count_or_dimension_mismatch() -> None:
    with pytest.raises(AppError, match="数量"):
        validate_embeddings(["a", "b"], [[0.0] * 1536], dimensions=1536)

    with pytest.raises(AppError, match="维度"):
        validate_embeddings(["a"], [[0.0] * 10], dimensions=1536)


@pytest.mark.asyncio
async def test_openai_compatible_provider_batches_and_orders_embeddings() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = request.read()
        requests.append({"url": str(request.url), "body": payload.decode("utf-8")})
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.2, 0.2]},
                    {"index": 0, "embedding": [0.1, 0.1]},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleEmbeddingProvider(
            client=client,
            base_url="https://embedding.example/v1",
            api_key="secret",
            model="embedding-model",
            dimensions=2,
            batch_size=2,
        )
        embeddings = await provider.embed_documents(["甲", "乙"])

    assert embeddings == [[0.1, 0.1], [0.2, 0.2]]
    assert requests[0]["url"] == "https://embedding.example/v1/embeddings"
    assert '"input":["甲","乙"]' in str(requests[0]["body"])

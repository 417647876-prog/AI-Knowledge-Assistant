import httpx
import pytest

from app.ai.embeddings import (
    FakeEmbeddingProvider,
    LocalEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    validate_embeddings,
)
from app.core.exceptions import AppError


@pytest.mark.asyncio
async def test_fake_embeddings_are_deterministic_and_512_dimensions() -> None:
    provider = FakeEmbeddingProvider(dimensions=512)

    first = await provider.embed_documents(["同一段文字"])
    second = await provider.embed_documents(["同一段文字"])
    query = await provider.embed_query("同一段文字")

    assert first == second
    assert first[0] == query
    assert len(first[0]) == 512


def test_validate_embeddings_rejects_count_or_dimension_mismatch() -> None:
    with pytest.raises(AppError, match="数量"):
        validate_embeddings(["a", "b"], [[0.0] * 512], dimensions=512)

    with pytest.raises(AppError, match="维度"):
        validate_embeddings(["a"], [[0.0] * 10], dimensions=512)


class FakeSentenceTransformer:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def encode(self, texts: list[str], **kwargs: object) -> list[list[float]]:
        self.calls.append((texts, kwargs))
        return [[float(index)] * 512 for index, _ in enumerate(texts, start=1)]


@pytest.mark.asyncio
async def test_local_provider_loads_once_and_normalizes_documents_and_query() -> None:
    model = FakeSentenceTransformer()
    factory_calls: list[tuple[str, str]] = []

    def factory(model_name: str, device: str) -> FakeSentenceTransformer:
        factory_calls.append((model_name, device))
        return model

    provider = LocalEmbeddingProvider(
        model_name="BAAI/bge-small-zh-v1.5",
        dimensions=512,
        batch_size=2,
        device="cpu",
        model_factory=factory,
    )

    documents = await provider.embed_documents(["甲", "乙"])
    query = await provider.embed_query("问题")

    assert factory_calls == [("BAAI/bge-small-zh-v1.5", "cpu")]
    assert len(documents) == 2
    assert len(query) == 512
    assert all(call[1]["normalize_embeddings"] is True for call in model.calls)
    assert all(call[1]["show_progress_bar"] is False for call in model.calls)
    assert model.calls[0][0] == ["甲", "乙"]
    assert model.calls[1][0] == ["为这个句子生成表示以用于检索相关文章：问题"]


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

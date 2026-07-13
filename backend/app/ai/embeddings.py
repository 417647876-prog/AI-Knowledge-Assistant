import hashlib

import httpx

from app.core.exceptions import AppError


def _provider_error(message: str) -> AppError:
    return AppError(code="EMBEDDING_PROVIDER_ERROR", message=message, status_code=502)


def validate_embeddings(
    texts: list[str], embeddings: list[list[float]], *, dimensions: int
) -> None:
    if len(texts) != len(embeddings):
        raise _provider_error("Embedding 返回数量与文本数量不一致。")
    if any(len(embedding) != dimensions for embedding in embeddings):
        raise _provider_error("Embedding 返回向量维度不正确。")


class FakeEmbeddingProvider:
    def __init__(self, *, dimensions: int) -> None:
        self._dimensions = dimensions

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        embeddings = [self._embed(text) for text in texts]
        validate_embeddings(texts, embeddings, dimensions=self._dimensions)
        return embeddings

    def _embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [
            ((digest[index % len(digest)] / 255.0) * 2.0) - 1.0 for index in range(self._dimensions)
        ]


class OpenAICompatibleEmbeddingProvider:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        model: str,
        dimensions: int,
        batch_size: int,
    ) -> None:
        self._client = client
        self._url = f"{base_url.rstrip('/')}/embeddings"
        self._api_key = api_key
        self._model = model
        self._dimensions = dimensions
        self._batch_size = batch_size

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        embeddings: list[list[float]] = []
        try:
            for start in range(0, len(texts), self._batch_size):
                batch = texts[start : start + self._batch_size]
                response = await self._client.post(
                    self._url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={"input": batch, "model": self._model, "dimensions": self._dimensions},
                )
                response.raise_for_status()
                data = sorted(response.json()["data"], key=lambda item: item["index"])
                embeddings.extend([item["embedding"] for item in data])
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            raise _provider_error("Embedding 服务暂不可用。") from error
        validate_embeddings(texts, embeddings, dimensions=self._dimensions)
        return embeddings

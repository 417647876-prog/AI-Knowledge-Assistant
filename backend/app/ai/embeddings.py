import asyncio
import hashlib
from collections.abc import Callable
from functools import lru_cache
from typing import Any

import httpx

from app.core.exceptions import AppError

BGE_ZH_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


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

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]

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

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]


def _load_sentence_transformer(model_name: str, device: str) -> Any:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name, device=device)


def _resolve_device(device: str) -> str:
    if device != "auto":
        return device
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


class LocalEmbeddingProvider:
    def __init__(
        self,
        *,
        model_name: str,
        dimensions: int,
        batch_size: int,
        device: str,
        model_factory: Callable[[str, str], Any] | None = None,
    ) -> None:
        self._model_name = model_name
        self._dimensions = dimensions
        self._batch_size = batch_size
        self._device = device
        self._model_factory = model_factory or _load_sentence_transformer
        self._model: Any | None = None
        self._model_lock = asyncio.Lock()

    async def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._model_lock:
            if self._model is None:
                device = await asyncio.to_thread(_resolve_device, self._device)
                self._model = await asyncio.to_thread(self._model_factory, self._model_name, device)
        return self._model

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = await self._get_model()
        result = await asyncio.to_thread(
            model.encode,
            texts,
            batch_size=self._batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        raw_embeddings = result.tolist() if hasattr(result, "tolist") else result
        embeddings = [[float(value) for value in row] for row in raw_embeddings]
        validate_embeddings(texts, embeddings, dimensions=self._dimensions)
        return embeddings

    async def embed_query(self, text: str) -> list[float]:
        instructed_query = f"{BGE_ZH_QUERY_INSTRUCTION}{text}"
        return (await self.embed_documents([instructed_query]))[0]


@lru_cache(maxsize=4)
def get_local_embedding_provider(
    model_name: str, dimensions: int, batch_size: int, device: str
) -> LocalEmbeddingProvider:
    return LocalEmbeddingProvider(
        model_name=model_name,
        dimensions=dimensions,
        batch_size=batch_size,
        device=device,
    )

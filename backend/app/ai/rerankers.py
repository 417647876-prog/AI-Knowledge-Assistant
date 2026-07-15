import asyncio
import threading
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from app.core.exceptions import AppError


def _provider_error() -> AppError:
    return AppError(
        code="RERANKER_PROVIDER_ERROR",
        message="重排序服务暂不可用。",
        status_code=502,
    )


def _load_cross_encoder(model_name: str, device: str) -> Any:
    from sentence_transformers import CrossEncoder

    return CrossEncoder(model_name, device=device)


def _resolve_device(device: str) -> str:
    if device != "auto":
        return device
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


class FakeRerankerProvider:
    def __init__(self, scores: list[float] | None = None) -> None:
        self._scores = list(scores) if scores is not None else None

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        if self._scores is not None:
            return list(self._scores)
        return [float(score) for score in range(len(documents), 0, -1)]


class LocalBgeRerankerProvider:
    def __init__(
        self,
        *,
        model_name: str,
        device: str,
        batch_size: int,
        model_factory: Callable[[str, str], Any] | None = None,
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._batch_size = batch_size
        self._model_factory = model_factory or _load_cross_encoder
        self._model: Any | None = None
        self._model_lock = asyncio.Lock()
        self._predict_lock = threading.Lock()

    async def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._model_lock:
            if self._model is None:
                device = await asyncio.to_thread(_resolve_device, self._device)
                self._model = await asyncio.to_thread(self._model_factory, self._model_name, device)
        return self._model

    def _predict(self, model: Any, pairs: list[list[str]]) -> Any:
        with self._predict_lock:
            return model.predict(
                pairs,
                batch_size=self._batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
            )

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        try:
            model = await self._get_model()
            pairs = [[query, document] for document in documents]
            result = await asyncio.to_thread(self._predict, model, pairs)
            raw_scores = result.tolist() if hasattr(result, "tolist") else result
            return [float(score) for score in raw_scores]
        except Exception as error:
            raise _provider_error() from error


_local_reranker_provider_cache_lock = threading.Lock()


@lru_cache(maxsize=4)
def _get_local_reranker_provider_cached(
    model_name: str, device: str, batch_size: int
) -> LocalBgeRerankerProvider:
    return LocalBgeRerankerProvider(
        model_name=model_name,
        device=device,
        batch_size=batch_size,
    )


def get_local_reranker_provider(
    model_name: str, device: str, batch_size: int
) -> LocalBgeRerankerProvider:
    with _local_reranker_provider_cache_lock:
        return _get_local_reranker_provider_cached(model_name, device, batch_size)


def _clear_local_reranker_provider_cache() -> None:
    with _local_reranker_provider_cache_lock:
        _get_local_reranker_provider_cached.cache_clear()


get_local_reranker_provider.cache_clear = (  # type: ignore[attr-defined]
    _clear_local_reranker_provider_cache
)

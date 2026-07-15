import threading

import pytest

from app.ai.rerankers import FakeRerankerProvider, LocalBgeRerankerProvider
from app.core.exceptions import AppError


@pytest.mark.asyncio
async def test_fake_reranker_returns_configured_scores() -> None:
    provider = FakeRerankerProvider(scores=[0.25, 0.75])

    result = await provider.rerank("年假", ["甲", "乙"])

    assert result == [0.25, 0.75]


@pytest.mark.asyncio
async def test_fake_reranker_returns_decreasing_default_scores() -> None:
    provider = FakeRerankerProvider()

    result = await provider.rerank("报销", ["甲", "乙", "丙"])

    assert result == [3.0, 2.0, 1.0]


class FakeCrossEncoder:
    def __init__(self, scores: list[float] | None = None) -> None:
        self._scores = scores or [0.8, 0.2]
        self.calls: list[tuple[list[list[str]], dict[str, object]]] = []
        self.predict_thread_ids: list[int] = []

    def predict(self, pairs: list[list[str]], **kwargs: object) -> list[float]:
        self.predict_thread_ids.append(threading.get_ident())
        self.calls.append((pairs, kwargs))
        return self._scores


@pytest.mark.asyncio
async def test_local_reranker_loads_once_and_predicts_in_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeCrossEncoder()
    factory_calls: list[tuple[str, str]] = []
    factory_thread_ids: list[int] = []

    def factory(model_name: str, device: str) -> FakeCrossEncoder:
        factory_thread_ids.append(threading.get_ident())
        factory_calls.append((model_name, device))
        return model

    monkeypatch.setattr("torch.cuda.is_available", lambda: True)
    event_loop_thread_id = threading.get_ident()
    provider = LocalBgeRerankerProvider(
        model_name="BAAI/bge-reranker-base",
        device="auto",
        batch_size=8,
        model_factory=factory,
    )

    first = await provider.rerank("年假", ["甲", "乙"])
    second = await provider.rerank("密码", ["丙", "丁"])

    assert first == [0.8, 0.2]
    assert second == [0.8, 0.2]
    assert factory_calls == [("BAAI/bge-reranker-base", "cuda")]
    assert factory_thread_ids[0] != event_loop_thread_id
    assert all(thread_id != event_loop_thread_id for thread_id in model.predict_thread_ids)
    assert model.calls == [
        (
            [["年假", "甲"], ["年假", "乙"]],
            {"batch_size": 8, "show_progress_bar": False, "convert_to_numpy": True},
        ),
        (
            [["密码", "丙"], ["密码", "丁"]],
            {"batch_size": 8, "show_progress_bar": False, "convert_to_numpy": True},
        ),
    ]


@pytest.mark.asyncio
async def test_local_reranker_returns_empty_without_loading_model() -> None:
    factory_called = False

    def factory(model_name: str, device: str) -> FakeCrossEncoder:
        nonlocal factory_called
        factory_called = True
        return FakeCrossEncoder()

    provider = LocalBgeRerankerProvider(
        model_name="unused",
        device="cpu",
        batch_size=4,
        model_factory=factory,
    )

    assert await provider.rerank("年假", []) == []
    assert factory_called is False


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_stage", ["load", "predict"])
async def test_local_reranker_converts_errors_without_leaking_sensitive_text(
    failing_stage: str,
) -> None:
    secret = "C:/Users/private/.cache/secret-model 年假原文"

    class FailingCrossEncoder(FakeCrossEncoder):
        def predict(self, pairs: list[list[str]], **kwargs: object) -> list[float]:
            raise RuntimeError(secret)

    def factory(model_name: str, device: str) -> FakeCrossEncoder:
        if failing_stage == "load":
            raise RuntimeError(secret)
        return FailingCrossEncoder()

    provider = LocalBgeRerankerProvider(
        model_name="private-model",
        device="cpu",
        batch_size=4,
        model_factory=factory,
    )

    with pytest.raises(AppError) as exc_info:
        await provider.rerank("年假原文", ["敏感片段"])

    assert exc_info.value.code == "RERANKER_PROVIDER_ERROR"
    assert exc_info.value.status_code == 502
    assert secret not in exc_info.value.message
    assert "年假原文" not in exc_info.value.message

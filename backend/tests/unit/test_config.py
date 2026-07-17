import socket
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_use_stage_1a_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("APP_NAME", "APP_ENV", "DATABASE_URL", "EMBEDDING_DIMENSIONS"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)

    assert settings.app_name == "AI 企业知识库助手"
    assert settings.app_env == "development"
    assert settings.database_url == (
        "postgresql+psycopg://knowledge:knowledge@localhost:5432/knowledge"
    )
    assert settings.embedding_dimensions == 512


def test_settings_reject_non_512_embedding_dimensions() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, embedding_dimensions=1536)


def test_settings_use_stage_1c_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.chunk_size == 800
    assert settings.chunk_overlap == 120
    assert settings.embedding_provider == "local"
    assert settings.embedding_model == "BAAI/bge-small-zh-v1.5"
    assert settings.embedding_device == "auto"
    assert settings.embedding_batch_size == 32


def test_settings_use_stage_1d_rag_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.chat_provider == "fake"
    assert settings.chat_base_url == "https://api.deepseek.com"
    assert settings.chat_model == "deepseek-v4-flash"
    assert settings.rag_top_k_default == 5
    assert settings.rag_top_k_max == 20
    assert settings.rag_score_threshold == 0.55
    assert settings.rag_question_max_length == 2000
    assert settings.rag_retrieval_mode == "vector"
    assert settings.rag_rrf_rank_constant == 60


def test_settings_use_stage_3c_reranker_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.rag_reranker_provider == "disabled"
    assert settings.rag_reranker_model == "BAAI/bge-reranker-base"
    assert settings.rag_reranker_device == "auto"
    assert settings.rag_reranker_batch_size == 16
    assert settings.rag_candidate_k == 20
    assert settings.rag_reranker_allow_fallback is True
    assert settings.rag_reranker_min_score is None


@pytest.mark.parametrize("invalid_score", [float("nan"), float("inf"), float("-inf")])
def test_settings_reject_non_finite_reranker_min_score(invalid_score: float) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, rag_reranker_min_score=invalid_score)


def test_settings_accepts_finite_reranker_min_score() -> None:
    settings = Settings(_env_file=None, rag_reranker_min_score=-2.75)

    assert settings.rag_reranker_min_score == -2.75


@pytest.mark.parametrize("candidate_k", [0, 101])
def test_settings_reject_invalid_candidate_k_bounds(candidate_k: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, rag_candidate_k=candidate_k)


def test_settings_reject_candidate_k_below_default_top_k() -> None:
    with pytest.raises(ValidationError, match="rag_candidate_k"):
        Settings(_env_file=None, rag_top_k_default=6, rag_candidate_k=5)


@pytest.mark.parametrize("rank_constant", [0, 1001])
def test_settings_reject_invalid_rrf_rank_constant(rank_constant: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, rag_rrf_rank_constant=rank_constant)


def test_settings_require_key_for_deepseek() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, chat_provider="deepseek", chat_api_key=None)


def test_chat_pricing_and_reservation_budgets_are_decimal_configuration() -> None:
    settings = Settings(
        _env_file=None,
        chat_cache_hit_input_price_per_million="0.125",
        chat_cache_miss_input_price_per_million="1.25",
        chat_output_price_per_million="2.50",
        chat_rewrite_input_token_reserve=2048,
        chat_rewrite_max_output_tokens=256,
        chat_answer_input_token_reserve=32768,
        chat_answer_max_output_tokens=4096,
    )

    assert settings.chat_cache_hit_input_price_per_million == Decimal("0.125")
    assert settings.chat_cache_miss_input_price_per_million == Decimal("1.25")
    assert settings.chat_output_price_per_million == Decimal("2.50")
    assert settings.chat_rewrite_input_token_reserve == 2048
    assert settings.chat_rewrite_max_output_tokens == 256
    assert settings.chat_answer_input_token_reserve == 32768
    assert settings.chat_answer_max_output_tokens == 4096


def test_deepseek_requires_explicit_positive_pricing() -> None:
    with pytest.raises(ValidationError, match="价格"):
        Settings(_env_file=None, chat_provider="deepseek", chat_api_key="secret")


def test_settings_reject_default_top_k_above_maximum() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, rag_top_k_default=6, rag_top_k_max=5)


def test_settings_reject_overlap_not_smaller_than_chunk_size() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, chunk_size=100, chunk_overlap=100)


def test_settings_use_authentication_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.access_token_expire_minutes == 15
    assert settings.refresh_token_expire_days == 7
    assert settings.refresh_cookie_secure is False


def test_settings_use_strict_stage_4_quota_and_rate_limit_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.login_rate_limit_window_seconds == 60
    assert settings.login_rate_limit_max_failures == 5
    assert settings.question_rate_limit_window_seconds == 60
    assert settings.question_rate_limit_max_requests == 10
    assert settings.default_daily_question_limit == 50
    assert settings.default_daily_upload_limit == 20
    assert settings.default_storage_bytes_limit == 500 * 1024**2
    assert settings.quota_timezone == "Asia/Shanghai"
    assert settings.global_cost_limit == Decimal("20.00")


def test_settings_reject_float_global_cost_and_gateway_without_secret() -> None:
    with pytest.raises(ValidationError, match="float"):
        Settings(_env_file=None, global_cost_limit=20.0)
    with pytest.raises(ValidationError, match="共享密钥"):
        Settings(_env_file=None, trusted_gateway_networks=("10.0.0.0/8",))


def test_settings_use_worker_runtime_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.worker_poll_seconds == 2
    assert settings.job_lease_seconds == 120
    assert settings.worker_heartbeat_seconds == 15
    assert settings.job_max_attempts == 3
    assert settings.job_retry_backoff_seconds == (30, 120)


def test_settings_default_worker_id_is_stable_for_the_host() -> None:
    settings = Settings(_env_file=None)

    assert settings.worker_id == socket.gethostname()


def test_production_settings_require_jwt_secret_key() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        Settings(_env_file=None, app_env="production", refresh_cookie_secure=True)


def test_production_settings_require_secure_refresh_cookie() -> None:
    with pytest.raises(ValidationError, match="REFRESH_COOKIE_SECURE"):
        Settings(_env_file=None, app_env="production", jwt_secret_key="x" * 64)

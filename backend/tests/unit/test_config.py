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


def test_settings_require_key_for_deepseek() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, chat_provider="deepseek", chat_api_key=None)


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


def test_production_settings_require_jwt_secret_key() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        Settings(_env_file=None, app_env="production", refresh_cookie_secure=True)


def test_production_settings_require_secure_refresh_cookie() -> None:
    with pytest.raises(ValidationError, match="REFRESH_COOKIE_SECURE"):
        Settings(_env_file=None, app_env="production", jwt_secret_key="x" * 64)

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
    assert settings.embedding_dimensions == 1536


def test_settings_reject_non_1536_embedding_dimensions() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, embedding_dimensions=1024)

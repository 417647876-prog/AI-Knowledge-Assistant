from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AI 企业知识库助手"
    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://knowledge:knowledge@localhost:5432/knowledge"
    embedding_dimensions: int = Field(default=1536, ge=1536, le=1536)
    upload_directory: Path = Path("uploads")
    max_upload_bytes: int = 20 * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()

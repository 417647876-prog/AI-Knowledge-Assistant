from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
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
    chunk_size: int = Field(default=800, gt=0)
    chunk_overlap: int = Field(default=120, ge=0)
    embedding_provider: Literal["fake", "openai"] = "fake"
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str | None = None
    embedding_model: str = "text-embedding-3-small"
    embedding_batch_size: int = Field(default=64, gt=0, le=2048)

    @model_validator(mode="after")
    def validate_chunk_settings(self) -> "Settings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap 必须小于 chunk_size")
        if self.embedding_provider == "openai" and not self.embedding_api_key:
            raise ValueError("使用 OpenAI Embedding 时必须配置 API Key")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

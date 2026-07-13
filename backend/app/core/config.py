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
    embedding_dimensions: int = Field(default=512, ge=512, le=512)
    upload_directory: Path = Path("uploads")
    max_upload_bytes: int = 20 * 1024 * 1024
    chunk_size: int = Field(default=800, gt=0)
    chunk_overlap: int = Field(default=120, ge=0)
    embedding_provider: Literal["fake", "local", "openai"] = "local"
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str | None = None
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_device: Literal["auto", "cuda", "cpu"] = "auto"
    embedding_batch_size: int = Field(default=32, gt=0, le=2048)
    chat_provider: Literal["fake", "deepseek"] = "fake"
    chat_base_url: str = "https://api.deepseek.com"
    chat_api_key: str | None = None
    chat_model: str = "deepseek-v4-flash"
    chat_timeout_seconds: float = Field(default=30.0, gt=0)
    rag_top_k_default: int = Field(default=5, ge=1, le=20)
    rag_top_k_max: int = Field(default=20, ge=1, le=100)
    rag_score_threshold: float = Field(default=0.55, ge=-1.0, le=1.0)
    rag_question_max_length: int = Field(default=2000, ge=1, le=10000)

    jwt_secret_key: str = "development-only-change-me-please-32-chars"
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_issuer: str = "ai-knowledge-assistant"
    jwt_audience: str = "ai-knowledge-assistant-web"
    access_token_expire_minutes: int = Field(default=15, ge=1, le=1440)
    refresh_token_expire_days: int = Field(default=7, ge=1, le=90)
    refresh_cookie_secure: bool = False
    trusted_origins: list[str] = [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]

    @model_validator(mode="after")
    def validate_chunk_settings(self) -> "Settings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap 必须小于 chunk_size")
        if self.embedding_provider == "openai" and not self.embedding_api_key:
            raise ValueError("使用 OpenAI Embedding 时必须配置 API Key")
        if self.chat_provider == "deepseek" and not self.chat_api_key:
            raise ValueError("使用 DeepSeek Chat 时必须配置 API Key")
        if self.rag_top_k_default > self.rag_top_k_max:
            raise ValueError("rag_top_k_default 不能大于 rag_top_k_max")
        if self.app_env == "production":
            if self.jwt_secret_key == "development-only-change-me-please-32-chars":
                raise ValueError("生产环境必须配置 JWT_SECRET_KEY")
            if not self.refresh_cookie_secure:
                raise ValueError("生产环境必须启用 REFRESH_COOKIE_SECURE")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

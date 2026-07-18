import socket
from decimal import Decimal
from functools import lru_cache
from ipaddress import ip_network
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
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
    upload_multipart_overhead_bytes: int = Field(default=1024 * 1024, ge=0)
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
    chat_cache_hit_input_price_per_million: Decimal = Field(default=Decimal("0"), ge=0)
    chat_cache_miss_input_price_per_million: Decimal = Field(default=Decimal("0"), ge=0)
    chat_output_price_per_million: Decimal = Field(default=Decimal("0"), ge=0)
    chat_rewrite_input_token_reserve: int = Field(default=4096, gt=0)
    chat_rewrite_max_output_tokens: int = Field(default=512, gt=0)
    chat_answer_input_token_reserve: int = Field(default=32768, gt=0)
    chat_answer_max_output_tokens: int = Field(default=4096, gt=0)
    rag_top_k_default: int = Field(default=5, ge=1, le=20)
    rag_top_k_max: int = Field(default=20, ge=1, le=100)
    rag_score_threshold: float = Field(default=0.55, ge=-1.0, le=1.0)
    rag_question_max_length: int = Field(default=2000, ge=1, le=10000)
    rag_retrieval_mode: Literal["vector", "hybrid"] = "vector"
    rag_rrf_rank_constant: int = Field(default=60, ge=1, le=1000)
    rag_reranker_provider: Literal["disabled", "fake", "local"] = "disabled"
    rag_reranker_model: str = "BAAI/bge-reranker-base"
    rag_reranker_device: Literal["auto", "cuda", "cpu"] = "auto"
    rag_reranker_batch_size: int = Field(default=16, ge=1, le=256)
    rag_candidate_k: int = Field(default=20, ge=1, le=100)
    rag_reranker_allow_fallback: bool = True
    rag_reranker_min_score: float | None = Field(default=None, allow_inf_nan=False)

    worker_id: str = Field(default_factory=socket.gethostname, min_length=1, max_length=255)
    worker_poll_seconds: float = Field(default=2, gt=0)
    job_lease_seconds: int = Field(default=120, gt=0)
    worker_heartbeat_seconds: float = Field(default=15, gt=0)
    job_max_attempts: int = Field(default=3, gt=0)
    job_retry_backoff_seconds: tuple[int, int] = (30, 120)
    trash_retention_days: int = Field(default=7, gt=0, le=365)

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
    login_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    login_rate_limit_max_failures: int = Field(default=5, ge=1, le=100)
    question_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    question_rate_limit_max_requests: int = Field(default=10, ge=1, le=1000)
    default_daily_question_limit: int = Field(default=50, ge=0, le=1_000_000)
    default_daily_upload_limit: int = Field(default=20, ge=0, le=1_000_000)
    default_storage_bytes_limit: int = Field(default=500 * 1024**2, ge=0)
    quota_timezone: str = "Asia/Shanghai"
    trusted_gateway_networks: tuple[str, ...] = ()
    gateway_shared_secret: str = ""
    internal_metrics_key: str = ""
    global_cost_limit: Decimal = Field(default=Decimal("20.00"), ge=0)

    @field_validator("global_cost_limit", mode="before")
    @classmethod
    def reject_float_cost(cls, value: object) -> object:
        if isinstance(value, float):
            raise ValueError("金额不得使用 float")
        return value

    @field_validator("trusted_gateway_networks")
    @classmethod
    def validate_gateway_networks(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            ip_network(value, strict=False)
        return values

    @model_validator(mode="after")
    def validate_chunk_settings(self) -> "Settings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap 必须小于 chunk_size")
        if self.embedding_provider == "openai" and not self.embedding_api_key:
            raise ValueError("使用 OpenAI Embedding 时必须配置 API Key")
        if self.chat_provider == "deepseek" and not self.chat_api_key:
            raise ValueError("使用 DeepSeek Chat 时必须配置 API Key")
        if self.chat_provider == "deepseek" and any(
            price <= 0
            for price in (
                self.chat_cache_hit_input_price_per_million,
                self.chat_cache_miss_input_price_per_million,
                self.chat_output_price_per_million,
            )
        ):
            raise ValueError("使用 DeepSeek Chat 时必须显式配置正数 Token 价格")
        if self.rag_top_k_default > self.rag_top_k_max:
            raise ValueError("rag_top_k_default 不能大于 rag_top_k_max")
        if self.rag_candidate_k < self.rag_top_k_default:
            raise ValueError("rag_candidate_k 不能小于 rag_top_k_default")
        if self.worker_heartbeat_seconds >= self.job_lease_seconds:
            raise ValueError("worker_heartbeat_seconds 必须小于 job_lease_seconds")
        if any(delay <= 0 for delay in self.job_retry_backoff_seconds):
            raise ValueError("job_retry_backoff_seconds 必须全部大于 0")
        if self.quota_timezone != "Asia/Shanghai":
            raise ValueError("quota_timezone 必须为 Asia/Shanghai")
        if self.trusted_gateway_networks and not self.gateway_shared_secret:
            raise ValueError("配置 gateway 网络时必须配置共享密钥")
        if self.app_env == "production":
            if (
                not self.jwt_secret_key
                or self.jwt_secret_key == "development-only-change-me-please-32-chars"
                or self.jwt_secret_key.startswith("REPLACE_WITH_")
            ):
                raise ValueError("生产环境必须配置 JWT_SECRET_KEY")
            if not self.refresh_cookie_secure:
                raise ValueError("生产环境必须启用 REFRESH_COOKIE_SECURE")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

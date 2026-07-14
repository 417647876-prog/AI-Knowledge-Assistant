# 阶段 1A：项目基础与数据库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立可测试的 FastAPI 后端骨架、统一配置与错误协议、PostgreSQL + pgvector 数据模型、Alembic 初始迁移，以及存活和就绪检查。

**Architecture:** 使用 FastAPI 应用工厂组织 HTTP 层，Pydantic Settings 管理环境配置，SQLAlchemy 2 异步会话管理数据库连接。业务模型由项目自行维护，Alembic 显式创建 pgvector 扩展及四张第一阶段核心表。

**Tech Stack:** Python 3.12、uv、FastAPI、Pydantic 2、SQLAlchemy 2、psycopg 3、pgvector、Alembic、pytest、Docker Compose、PostgreSQL 16。

## Global Constraints

- 项目根目录固定为 `D:\学习\AI-Knowledge-Assistant`。
- Python 固定为 3.12；本机通过 `uv python install 3.12` 安装，不修改系统 Python 3.13。
- 第一阶段向量维度固定为 1536。
- PostgreSQL 固定使用 16 系列及 pgvector 扩展。
- 所有主键使用 UUID；所有时间使用数据库 `TIMESTAMPTZ` 和 UTC。
- 配置与密钥只从环境变量或 `.env` 读取；真实 `.env` 不提交。
- 测试遵循 Red-Green-Refactor；每个生产行为必须先看到对应测试因缺少该行为而失败。
- 当前机器未检测到 Docker 和本地 PostgreSQL；Task 6 的数据库集成验证只有在用户完成 Docker Desktop 安装并启动后执行。
- 不实现知识库 API、文件上传、解析、Embedding、问答、登录或 Vue 前端；这些属于阶段 1B 之后。

## 文件职责图

```text
backend/pyproject.toml                 Python 依赖、pytest 与 Ruff 配置
backend/.python-version                固定 Python 3.12
backend/.env.example                   可提交的本地配置示例
backend/app/core/config.py             强类型应用配置
backend/app/core/exceptions.py         统一业务异常
backend/app/api/error_handlers.py      异常到 HTTP 错误响应的映射
backend/app/api/middleware.py          request_id 生成与透传
backend/app/api/v1/health.py           /health 与 /ready
backend/app/db/base.py                 SQLAlchemy Base 与命名约定
backend/app/db/session.py              AsyncEngine 和 AsyncSession
backend/app/db/health.py               PostgreSQL/pgvector 就绪检查
backend/app/db/models/*.py             四张核心业务表
backend/migrations/*                   Alembic 迁移环境与初始迁移
deploy/docker-compose.yml              PostgreSQL 16 + pgvector 本地服务
backend/tests/unit/*                   无外部服务的快速测试
backend/tests/integration/*            显式开启的真实数据库测试
```

---

### Task 1: Python 项目与强类型配置

**Files:**
- Create: `backend/.python-version`
- Create: `backend/pyproject.toml`
- Create: `backend/.gitignore`
- Create: `backend/.env.example`
- Create: `backend/app/__init__.py`
- Create: `backend/app/core/__init__.py`
- Create: `backend/app/core/config.py`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/unit/__init__.py`
- Test: `backend/tests/unit/test_config.py`

**Interfaces:**
- Consumes: 环境变量 `APP_ENV`、`DATABASE_URL`、`EMBEDDING_DIMENSIONS`。
- Produces: `Settings`、`get_settings() -> Settings`。

- [ ] **Step 1: 安装并固定 Python 3.12**

Run from `D:\学习\AI-Knowledge-Assistant\backend` after creating that directory:

```powershell
uv python install 3.12
Set-Content -Encoding ascii .python-version '3.12'
```

Expected: `uv python find 3.12` 返回 uv 管理的 Python 3.12 路径，且没有替换系统 Python 3.13。

- [ ] **Step 2: 写配置失败测试**

Create `backend/tests/unit/test_config.py`:

```python
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
```

- [ ] **Step 3: 创建项目依赖声明并同步环境**

Create `backend/pyproject.toml`:

```toml
[project]
name = "ai-knowledge-assistant-backend"
version = "0.1.0"
description = "AI 企业知识库助手后端"
requires-python = ">=3.12,<3.13"
dependencies = [
  "alembic>=1.14,<2",
  "fastapi>=0.115,<1",
  "pgvector>=0.4,<1",
  "psycopg[binary]>=3.2,<4",
  "pydantic-settings>=2.7,<3",
  "sqlalchemy[asyncio]>=2.0,<3",
  "uvicorn[standard]>=0.34,<1",
]

[dependency-groups]
dev = [
  "httpx>=0.28,<1",
  "pytest>=8.3,<10",
  "pytest-asyncio>=0.25,<2",
  "ruff>=0.9,<1",
]

[tool.pytest.ini_options]
addopts = "-ra"
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
  "integration: requires PostgreSQL + pgvector",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

Create `backend/.gitignore`:

```gitignore
.env
.venv/
__pycache__/
.pytest_cache/
.ruff_cache/
*.py[cod]
htmlcov/
.coverage
```

Create `backend/.env.example`:

```dotenv
APP_NAME=AI 企业知识库助手
APP_ENV=development
DATABASE_URL=postgresql+psycopg://knowledge:knowledge@localhost:5432/knowledge
EMBEDDING_DIMENSIONS=1536
```

Create each package marker with its shown content:

```python
# backend/app/__init__.py
"""AI 企业知识库助手后端。"""

# backend/app/core/__init__.py
"""应用核心配置与公共能力。"""

# backend/tests/__init__.py
"""测试包。"""

# backend/tests/unit/__init__.py
"""单元测试。"""
```

Run:

```powershell
uv sync --dev
uv run pytest tests/unit/test_config.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'app.core.config'`。

- [ ] **Step 4: 实现最小配置对象**

Create `backend/app/core/config.py`:

```python
from functools import lru_cache
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
    database_url: str = (
        "postgresql+psycopg://knowledge:knowledge@localhost:5432/knowledge"
    )
    embedding_dimensions: int = Field(default=1536, ge=1536, le=1536)


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 5: 验证测试、格式与类型边界**

Run:

```powershell
uv run pytest tests/unit/test_config.py -v
uv run ruff check app tests
uv run ruff format --check app tests
```

Expected: 2 tests pass，Ruff 两条命令均 exit 0。

- [ ] **Step 6: 提交 Task 1**

```powershell
git add backend/.python-version backend/pyproject.toml backend/uv.lock backend/.gitignore backend/.env.example backend/app backend/tests
git commit -m "chore: 初始化 FastAPI 后端配置"
```

---

### Task 2: FastAPI 应用工厂与存活检查

**Files:**
- Create: `backend/app/api/__init__.py`
- Create: `backend/app/api/v1/__init__.py`
- Create: `backend/app/api/v1/health.py`
- Create: `backend/app/main.py`
- Test: `backend/tests/unit/test_health_api.py`

**Interfaces:**
- Consumes: `get_settings() -> Settings`。
- Produces: `create_app() -> FastAPI`、`GET /health`。

- [ ] **Step 1: 写存活检查失败测试**

Create `backend/tests/unit/test_health_api.py`:

```python
from fastapi.testclient import TestClient

from app.main import create_app


def test_health_returns_application_status() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "AI 企业知识库助手",
    }
```

Run:

```powershell
uv run pytest tests/unit/test_health_api.py -v
```

Expected: fails with `ModuleNotFoundError: No module named 'app.main'`。

- [ ] **Step 2: 实现路由与应用工厂**

Create package markers:

```python
# backend/app/api/__init__.py
"""HTTP API。"""

# backend/app/api/v1/__init__.py
"""第一版 API 路由。"""
```

Create `backend/app/api/v1/health.py`:

```python
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service=get_settings().app_name)
```

Create `backend/app/main.py`:

```python
from fastapi import FastAPI

from app.api.v1.health import router as health_router
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.include_router(health_router)
    return app


app = create_app()
```

- [ ] **Step 3: 验证最小应用**

Run:

```powershell
uv run pytest tests/unit/test_health_api.py -v
uv run pytest -v
uv run ruff check app tests
```

Expected: 3 tests pass，Ruff exit 0。

- [ ] **Step 4: 提交 Task 2**

```powershell
git add backend/app/api backend/app/main.py backend/tests/unit/test_health_api.py
git commit -m "feat: 添加应用工厂与存活检查"
```

---

### Task 3: request_id 与统一错误协议

**Files:**
- Create: `backend/app/core/exceptions.py`
- Create: `backend/app/api/middleware.py`
- Create: `backend/app/api/error_handlers.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/unit/test_error_contract.py`

**Interfaces:**
- Consumes: `AppError(code, message, status_code)`。
- Produces: `X-Request-ID` 响应头和 `{ "error": { ... } }` 错误响应。

- [ ] **Step 1: 写统一错误失败测试**

Create `backend/tests/unit/test_error_contract.py`:

```python
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.core.exceptions import AppError
from app.main import create_app


def test_app_error_uses_request_id_and_safe_envelope() -> None:
    app = create_app()
    router = APIRouter()

    @router.get("/_test/error")
    async def raise_error() -> None:
        raise AppError(
            code="DOCUMENT_NOT_FOUND",
            message="文档不存在。",
            status_code=404,
        )

    app.include_router(router)
    client = TestClient(app)

    response = client.get(
        "/_test/error",
        headers={"X-Request-ID": "test-request-001"},
    )

    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == "test-request-001"
    assert response.json() == {
        "error": {
            "code": "DOCUMENT_NOT_FOUND",
            "message": "文档不存在。",
            "request_id": "test-request-001",
        }
    }
```

Run:

```powershell
uv run pytest tests/unit/test_error_contract.py -v
```

Expected: fails because `app.core.exceptions` does not exist。

- [ ] **Step 2: 实现异常、Middleware 与 Handler**

Create `backend/app/core/exceptions.py`:

```python
class AppError(Exception):
    def __init__(self, *, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
```

Create `backend/app/api/middleware.py`:

```python
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

request_id_context: ContextVar[str] = ContextVar("request_id", default="")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        token = request_id_context.set(request_id)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            request_id_context.reset(token)
```

Create `backend/app/api/error_handlers.py`:

```python
from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.exceptions import AppError


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "request_id": request_id,
            }
        },
    )
```

Replace `backend/app/main.py` with:

```python
from fastapi import FastAPI

from app.api.error_handlers import app_error_handler
from app.api.middleware import RequestIdMiddleware
from app.api.v1.health import router as health_router
from app.core.config import get_settings
from app.core.exceptions import AppError


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.include_router(health_router)
    return app


app = create_app()
```

- [ ] **Step 3: 验证错误协议没有破坏健康检查**

Run:

```powershell
uv run pytest tests/unit/test_error_contract.py tests/unit/test_health_api.py -v
uv run pytest -v
uv run ruff check app tests
```

Expected: 4 tests pass，错误响应不包含 Python 堆栈或服务器路径。

- [ ] **Step 4: 提交 Task 3**

```powershell
git add backend/app/api backend/app/core/exceptions.py backend/app/main.py backend/tests/unit/test_error_contract.py
git commit -m "feat: 统一请求追踪与错误响应"
```

---

### Task 4: 异步数据库会话与就绪检查

**Files:**
- Create: `backend/app/db/__init__.py`
- Create: `backend/app/db/session.py`
- Create: `backend/app/db/health.py`
- Create: `backend/app/api/dependencies.py`
- Modify: `backend/app/api/v1/health.py`
- Test: `backend/tests/unit/test_ready_api.py`

**Interfaces:**
- Consumes: `Settings.database_url`、FastAPI dependency override。
- Produces: `get_session() -> AsyncIterator[AsyncSession]`、`check_database(session) -> bool`、`GET /ready`。

- [ ] **Step 1: 写就绪检查失败测试**

Create `backend/tests/unit/test_ready_api.py`:

```python
from fastapi.testclient import TestClient

from app.api.dependencies import database_is_ready
from app.main import create_app


async def ready_database() -> bool:
    return True


async def unavailable_database() -> bool:
    return False


def test_ready_returns_ok_when_database_and_pgvector_are_ready() -> None:
    app = create_app()
    app.dependency_overrides[database_is_ready] = ready_database

    response = TestClient(app).get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_ready_returns_503_when_database_is_unavailable() -> None:
    app = create_app()
    app.dependency_overrides[database_is_ready] = unavailable_database

    response = TestClient(app).get("/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
```

Run:

```powershell
uv run pytest tests/unit/test_ready_api.py -v
```

Expected: fails because `app.api.dependencies` does not exist。

- [ ] **Step 2: 实现数据库会话与探针**

Create `backend/app/db/__init__.py`:

```python
"""数据库基础设施。"""
```

Create `backend/app/db/session.py`:

```python
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
```

Create `backend/app/db/health.py`:

```python
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def check_database(session: AsyncSession) -> bool:
    result = await session.execute(
        text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
    )
    return bool(result.scalar_one())
```

Create `backend/app/api/dependencies.py`:

```python
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.health import check_database
from app.db.session import get_session


async def database_is_ready(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> bool:
    return await check_database(session)
```

Replace `backend/app/api/v1/health.py` with:

```python
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.dependencies import database_is_ready
from app.core.config import get_settings
from app.core.exceptions import AppError

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str


class ReadyResponse(BaseModel):
    status: Literal["ready"]


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service=get_settings().app_name)


@router.get("/ready", response_model=ReadyResponse)
async def ready(
    is_ready: Annotated[bool, Depends(database_is_ready)],
) -> ReadyResponse:
    if not is_ready:
        raise AppError(
            code="DATABASE_UNAVAILABLE",
            message="数据库暂不可用。",
            status_code=503,
        )
    return ReadyResponse(status="ready")
```

- [ ] **Step 3: 验证依赖覆盖和错误响应**

Run:

```powershell
uv run pytest tests/unit/test_ready_api.py -v
uv run pytest -v
uv run ruff check app tests
```

Expected: 6 tests pass；单元测试不连接真实数据库。

- [ ] **Step 4: 提交 Task 4**

```powershell
git add backend/app/api backend/app/db backend/tests/unit/test_ready_api.py
git commit -m "feat: 添加数据库会话与就绪检查"
```

---

### Task 5: SQLAlchemy 核心数据模型

**Files:**
- Create: `backend/app/db/base.py`
- Create: `backend/app/db/models/__init__.py`
- Create: `backend/app/db/models/knowledge_base.py`
- Create: `backend/app/db/models/document.py`
- Create: `backend/app/db/models/document_chunk.py`
- Create: `backend/app/db/models/ingestion_job.py`
- Test: `backend/tests/unit/test_database_metadata.py`

**Interfaces:**
- Consumes: SQLAlchemy 2、`pgvector.sqlalchemy.VECTOR`。
- Produces: `Base.metadata` 中的 `knowledge_bases`、`documents`、`document_chunks`、`ingestion_jobs`。

- [ ] **Step 1: 写模型元数据失败测试**

Create `backend/tests/unit/test_database_metadata.py`:

```python
from sqlalchemy import UniqueConstraint

from app.db.base import Base
from app.db.models import Document, DocumentChunk, IngestionJob, KnowledgeBase


def test_stage_1a_metadata_contains_four_core_tables() -> None:
    assert set(Base.metadata.tables) == {
        "knowledge_bases",
        "documents",
        "document_chunks",
        "ingestion_jobs",
    }
    assert KnowledgeBase.__tablename__ == "knowledge_bases"
    assert Document.__tablename__ == "documents"
    assert DocumentChunk.__tablename__ == "document_chunks"
    assert IngestionJob.__tablename__ == "ingestion_jobs"


def test_document_duplicate_constraint_is_scoped_to_knowledge_base() -> None:
    constraints = [
        constraint
        for constraint in Document.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    column_sets = [{column.name for column in item.columns} for item in constraints]

    assert {"knowledge_base_id", "file_hash"} in column_sets


def test_document_chunk_embedding_is_1536_dimensions() -> None:
    embedding_type = DocumentChunk.__table__.c.embedding.type

    assert embedding_type.dim == 1536
```

Run:

```powershell
uv run pytest tests/unit/test_database_metadata.py -v
```

Expected: fails because `app.db.base` and model modules do not exist。

- [ ] **Step 2: 创建 Base、时间 Mixin 和模型**

Create `backend/app/db/base.py`:

```python
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=naming_convention)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
```

Create `backend/app/db/models/knowledge_base.py`:

```python
from uuid import UUID, uuid4

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class KnowledgeBase(TimestampMixin, Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
```

Create `backend/app/db/models/document.py`:

```python
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Document(TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint(
            "knowledge_base_id",
            "file_hash",
            name="uq_documents_knowledge_base_id_file_hash",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    original_file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_extension: Mapped[str] = mapped_column(String(20), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Create `backend/app/db/models/document_chunk.py`:

```python
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class DocumentChunk(TimestampMixin, Base):
    __tablename__ = "document_chunks"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sheet_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    row_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    start_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    embedding: Mapped[list[float]] = mapped_column(VECTOR(1536), nullable=False)
```

Create `backend/app/db/models/ingestion_job.py`:

```python
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    stage: Mapped[str] = mapped_column(String(30), nullable=False, default="parse")
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Create `backend/app/db/models/__init__.py`:

```python
from app.db.models.document import Document
from app.db.models.document_chunk import DocumentChunk
from app.db.models.ingestion_job import IngestionJob
from app.db.models.knowledge_base import KnowledgeBase

__all__ = ["Document", "DocumentChunk", "IngestionJob", "KnowledgeBase"]
```

- [ ] **Step 3: 验证模型元数据**

Run:

```powershell
uv run pytest tests/unit/test_database_metadata.py -v
uv run pytest -v
uv run ruff check app tests
```

Expected: 9 tests pass；向量维度断言为 1536。

- [ ] **Step 4: 提交 Task 5**

```powershell
git add backend/app/db backend/tests/unit/test_database_metadata.py
git commit -m "feat: 添加知识库核心数据模型"
```

---

### Task 6: Docker PostgreSQL、Alembic 初始迁移与集成验证

**Files:**
- Create: `deploy/docker-compose.yml`
- Create: `backend/alembic.ini`
- Create: `backend/migrations/env.py`
- Create: `backend/migrations/script.py.mako`
- Create: `backend/migrations/versions/20260710_01_initial_schema.py`
- Create: `backend/tests/integration/__init__.py`
- Create: `backend/tests/integration/test_database_schema.py`

**Interfaces:**
- Consumes: `DATABASE_URL`、Docker PostgreSQL 16 + pgvector。
- Produces: 可重复执行的 `alembic upgrade head`，以及真实 `/ready` 所依赖的 vector 扩展。

- [ ] **Step 1: 确认 Docker 前置条件**

Run:

```powershell
wsl --version
docker --version
docker compose version
```

Expected: WSL 版本信息可读，Docker 与 Compose 均返回版本。若 Docker 命令不存在，停止 Task 6；不要用 SQLite 代替 PostgreSQL，因为 SQLite 无法验证 pgvector、JSONB、PostgreSQL UUID 和迁移 SQL。

- [ ] **Step 2: 写数据库结构集成失败测试**

Create `backend/tests/integration/__init__.py`:

```python
"""需要外部基础设施的集成测试。"""
```

Create `backend/tests/integration/test_database_schema.py`:

```python
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="设置 RUN_DATABASE_TESTS=1 后运行 PostgreSQL 集成测试",
    ),
]


@pytest.mark.asyncio
async def test_initial_migration_creates_pgvector_and_core_tables() -> None:
    database_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            extension_exists = await connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector')")
            )
            table_rows = await connection.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname='public' AND tablename IN "
                    "('knowledge_bases','documents','document_chunks','ingestion_jobs')"
                )
            )
            table_names = {row[0] for row in table_rows}
    finally:
        await engine.dispose()

    assert extension_exists is True
    assert table_names == {
        "knowledge_bases",
        "documents",
        "document_chunks",
        "ingestion_jobs",
    }
```

- [ ] **Step 3: 创建 PostgreSQL + pgvector Compose 服务**

Create `deploy/docker-compose.yml`:

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: knowledge
      POSTGRES_USER: knowledge
      POSTGRES_PASSWORD: knowledge
    ports:
      - "5432:5432"
    volumes:
      - knowledge_postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U knowledge -d knowledge"]
      interval: 5s
      timeout: 5s
      retries: 10

volumes:
  knowledge_postgres_data:
```

Run from repository root:

```powershell
docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml ps
```

Expected: `postgres` 状态最终显示 healthy。

Run from `backend` before creating any migration:

```powershell
$env:RUN_DATABASE_TESTS='1'
$env:DATABASE_URL='postgresql+psycopg://knowledge:knowledge@localhost:5432/knowledge'
uv run pytest tests/integration/test_database_schema.py -v
```

Expected: test fails at the table-name assertion because the four application tables do not exist yet；the database connection itself succeeds。

- [ ] **Step 4: 创建 Alembic 环境**

Create `backend/alembic.ini`:

```ini
[alembic]
script_location = migrations
prepend_sys_path = .

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

Create `backend/migrations/env.py`:

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.db.base import Base
from app.db.models import Document, DocumentChunk, IngestionJob, KnowledgeBase  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

Create `backend/migrations/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 5: 创建显式初始迁移**

Create `backend/migrations/versions/20260710_01_initial_schema.py`:

```python
from collections.abc import Sequence

from alembic import op
import pgvector.sqlalchemy
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260710_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "knowledge_bases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_bases"),
    )
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_file_name", sa.String(length=255), nullable=False),
        sa.Column("stored_file_name", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("file_extension", sa.String(length=20), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            name="fk_documents_knowledge_base_id_knowledge_bases",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.UniqueConstraint(
            "knowledge_base_id",
            "file_hash",
            name="uq_documents_knowledge_base_id_file_hash",
        ),
    )
    op.create_index(
        "ix_documents_knowledge_base_id",
        "documents",
        ["knowledge_base_id"],
        unique=False,
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("sheet_name", sa.String(length=100), nullable=True),
        sa.Column("row_start", sa.Integer(), nullable=True),
        sa.Column("section_title", sa.String(length=500), nullable=True),
        sa.Column("start_index", sa.Integer(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.VECTOR(dim=1536), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_chunks_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            name="fk_document_chunks_knowledge_base_id_knowledge_bases",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_chunks"),
    )
    op.create_index(
        "ix_document_chunks_document_id",
        "document_chunks",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        "ix_document_chunks_knowledge_base_id",
        "document_chunks",
        ["knowledge_base_id"],
        unique=False,
    )
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("stage", sa.String(length=30), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_ingestion_jobs_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ingestion_jobs"),
    )
    op.create_index(
        "ix_ingestion_jobs_document_id",
        "ingestion_jobs",
        ["document_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ingestion_jobs_document_id", table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")
    op.drop_index("ix_document_chunks_knowledge_base_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_document_id", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index("ix_documents_knowledge_base_id", table_name="documents")
    op.drop_table("documents")
    op.drop_table("knowledge_bases")
    op.execute("DROP EXTENSION IF EXISTS vector")
```

Run a drift check before applying the migration:

```powershell
uv run alembic upgrade head
uv run alembic check
```

Expected: upgrade succeeds；`alembic check` reports `No new upgrade operations detected.`

- [ ] **Step 6: 运行迁移与真实集成测试**

Run from `backend`:

```powershell
$env:DATABASE_URL='postgresql+psycopg://knowledge:knowledge@localhost:5432/knowledge'
uv run alembic upgrade head
$env:RUN_DATABASE_TESTS='1'
uv run pytest tests/integration/test_database_schema.py -v
uv run pytest -v
```

Expected: migration succeeds；1 integration test passes；全部单元测试继续通过。

- [ ] **Step 7: 验证真实就绪端点**

Run terminal A from `backend`:

```powershell
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --loop app.core.event_loop:new_event_loop
```

Run terminal B:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/ready
```

Expected: `/health` returns `status=ok`；`/ready` returns `status=ready`。

- [ ] **Step 8: 迁移往返验证**

Run only against the local disposable Docker database:

```powershell
uv run alembic downgrade base
uv run alembic upgrade head
$env:RUN_DATABASE_TESTS='1'
uv run pytest tests/integration/test_database_schema.py -v
```

Expected: downgrade and second upgrade both succeed；integration test remains green。

- [ ] **Step 9: 最终静态检查并提交 Task 6**

```powershell
uv run ruff check app tests migrations
uv run ruff format --check app tests migrations
git add deploy backend/alembic.ini backend/migrations backend/tests/integration
git commit -m "feat: 添加 PostgreSQL pgvector 初始迁移"
```

Expected: working tree clean，阶段 1A 的六个任务均有独立提交。

## 阶段 1A 完成检查

```powershell
git status --short
git log --oneline -7
Set-Location backend
uv run pytest -v
uv run ruff check app tests migrations
uv run alembic current
```

成功标准：

- Git 工作区无未提交文件。
- Python 运行时为 3.12。
- 所有单元测试通过。
- Docker 可用时，数据库集成测试通过。
- Alembic 当前版本为 `20260710_01 (head)`。
- `/health` 不依赖数据库即可返回 200。
- `/ready` 只有在 PostgreSQL 可连接且 pgvector 已启用时返回 200。
- 数据库包含四张核心表和 `vector(1536)` 字段。

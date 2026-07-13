# 阶段 2B：认证、角色权限与知识库隔离 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为现有 FastAPI + Vue 知识库工作台增加管理员创建账号、JWT 登录、可撤销刷新会话、角色权限和知识库数据隔离。

**Architecture:** FastAPI 使用 Argon2id 校验密码、短期 JWT Access Token 和数据库可撤销的随机 Refresh Token。统一认证依赖从数据库确认用户状态，统一授权服务按 `knowledge_bases.owner_id` 过滤资源；Vue 使用 Pinia 内存保存 Access Token，通过 HttpOnly Cookie 恢复会话，并由 Vue Router 保护工作台和管理员页面。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、Alembic、PostgreSQL、`pwdlib[argon2]`、PyJWT、Vue 3、TypeScript 5.9、Pinia 3、Vue Router 4、Element Plus、Vitest、pytest。

## Global Constraints

- 只定义 `admin` 和 `user` 两种角色；不增加第三种角色或细粒度权限系统。
- 不提供公开注册；账号只能由管理员创建。
- Access Token 默认 15 分钟，只保存在前端内存；Refresh Token 默认 7 天，只通过 HttpOnly Cookie 发送，数据库只保存哈希。
- 普通用户只能访问自己的知识库、文档和问答；管理员可以访问全部。
- 普通用户访问他人资源返回 404；普通用户调用管理员接口返回 403。
- `/health`、`/ready` 和 OpenAPI 保持匿名可用，其他现有业务接口必须认证。
- 登录失败统一返回“用户名或密码错误”，不能暴露用户名是否存在或账号是否停用。
- 不能停用自己，系统必须保留至少一个启用的管理员。
- 本地测试数据只能通过显式开发命令清理；Alembic 迁移不得自动删除业务数据。
- 生产环境按同一 HTTPS 域名和 `/api` 反向代理设计；本阶段不实施公网部署。
- 不实现邮件、找回密码、首次登录强制改密、MFA、OAuth、用户删除、审计日志、文档删除、流式回答和聊天历史。
- PowerShell 使用 `npm.cmd`，后端命令在 `backend/` 下使用 `uv run`。
- 所有依赖 PostgreSQL 的新增测试沿用 `integration` marker 和 `RUN_DATABASE_TESTS=1` 开关；权限测试不得用纯 mock 代替真实数据库约束。
- 每个任务严格 TDD：先得到预期失败，再写最小实现；每个任务只提交自身范围。

---

## 文件职责图

### 后端新增文件

- `backend/app/core/security.py`：密码哈希、JWT 和 Refresh Token 纯函数。
- `backend/app/db/models/user.py`：用户模型和角色常量。
- `backend/app/db/models/refresh_session.py`：可撤销刷新会话模型。
- `backend/app/auth/schemas.py`：认证和当前用户响应模型。
- `backend/app/auth/service.py`：登录、刷新、退出、会话撤销业务。
- `backend/app/authorization/service.py`：知识库和文档归属查询。
- `backend/app/api/auth_dependencies.py`：`get_current_user` 与 `require_admin`。
- `backend/app/api/v1/auth.py`：登录、刷新、退出、当前用户接口。
- `backend/app/api/v1/admin_users.py`：管理员用户管理接口。
- `backend/scripts/reset_development_data.py`：受保护的本地数据清理命令。
- `backend/scripts/create_admin.py`：首个管理员初始化命令。
- `backend/migrations/versions/20260713_03_auth_rbac.py`：用户、刷新会话和知识库所有者约束迁移。

### 前端新增文件

- `frontend/src/api/auth.ts`：登录、刷新、退出和当前用户 API。
- `frontend/src/api/adminUsers.ts`：管理员用户 API。
- `frontend/src/stores/auth.ts`：Access Token、当前用户和恢复流程。
- `frontend/src/stores/adminUsers.ts`：管理员用户列表和变更状态。
- `frontend/src/router/index.ts`：路由与认证守卫。
- `frontend/src/views/LoginView.vue`：登录页。
- `frontend/src/views/WorkspaceView.vue`：从当前 `App.vue` 提取的工作台。
- `frontend/src/views/AdminUsersView.vue`：用户管理页。
- `frontend/src/views/ForbiddenView.vue`：403 页面。
- `frontend/src/components/AppHeader.vue`：当前用户、管理员入口和退出。

---

### Task 1: 密码、JWT 与配置基础

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`
- Modify: `backend/app/core/config.py`
- Create: `backend/app/core/security.py`
- Modify: `backend/tests/unit/test_config.py`
- Create: `backend/tests/unit/test_security.py`

**Interfaces:**
- Produces: `hash_password(password: str) -> str`
- Produces: `verify_password(password: str, password_hash: str) -> bool`
- Produces: `create_access_token(*, user_id: UUID, role: str, settings: Settings, now: datetime | None = None) -> str`
- Produces: `decode_access_token(token: str, settings: Settings, now: datetime | None = None) -> AccessTokenClaims`
- Produces: `create_refresh_token() -> RefreshTokenParts`
- Produces: `hash_refresh_secret(secret: str) -> str`
- Produces: `Settings` 中 JWT、Cookie 和可信来源配置。

- [ ] **Step 1: 为配置和安全纯函数写失败测试**

在 `test_security.py` 覆盖下面的具体行为：

```python
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.core.security import (
    TokenValidationError,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    hash_password,
    hash_refresh_secret,
    verify_password,
)


def test_password_is_argon2_and_verifies() -> None:
    encoded = hash_password("correct horse battery")
    assert encoded.startswith("$argon2")
    assert verify_password("correct horse battery", encoded) is True
    assert verify_password("wrong password", encoded) is False


def test_access_token_contains_fixed_issuer_audience_and_expiry() -> None:
    now = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
    settings = Settings(_env_file=None, jwt_secret_key="x" * 64)
    user_id = uuid4()
    token = create_access_token(user_id=user_id, role="admin", settings=settings, now=now)
    claims = decode_access_token(token, settings, now=now + timedelta(minutes=1))
    assert claims.user_id == user_id
    assert claims.role == "admin"
    assert claims.expires_at == now + timedelta(minutes=15)


def test_expired_access_token_is_rejected() -> None:
    now = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
    settings = Settings(_env_file=None, jwt_secret_key="x" * 64)
    token = create_access_token(user_id=uuid4(), role="user", settings=settings, now=now)
    with pytest.raises(TokenValidationError, match="TOKEN_EXPIRED"):
        decode_access_token(token, settings, now=now + timedelta(minutes=16))


def test_refresh_token_only_exposes_hashable_random_secret() -> None:
    token = create_refresh_token()
    assert token.raw == f"{token.session_id}.{token.secret}"
    assert len(hash_refresh_secret(token.secret)) == 64
    assert token.secret not in hash_refresh_secret(token.secret)
```

在 `test_config.py` 断言默认值为 15 分钟、7 天、开发环境 Cookie 非 Secure，并断言生产环境缺少 `JWT_SECRET_KEY` 时校验失败。

- [ ] **Step 2: 运行测试确认预期失败**

Run: `uv run pytest tests/unit/test_security.py tests/unit/test_config.py -q`

Expected: FAIL，原因是 `app.core.security` 不存在或 `Settings` 尚无 JWT 字段。

- [ ] **Step 3: 添加依赖和最小安全实现**

Run:

```powershell
uv add "pwdlib[argon2]>=0.3,<1" "PyJWT>=2.10,<3"
```

`Settings` 增加以下精确字段：

```python
jwt_secret_key: str = "development-only-change-me-please-32-chars"
jwt_algorithm: Literal["HS256"] = "HS256"
jwt_issuer: str = "ai-knowledge-assistant"
jwt_audience: str = "ai-knowledge-assistant-web"
access_token_expire_minutes: int = Field(default=15, ge=1, le=1440)
refresh_token_expire_days: int = Field(default=7, ge=1, le=90)
refresh_cookie_secure: bool = False
trusted_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]
```

生产配置校验必须拒绝默认开发密钥和 `refresh_cookie_secure=False`。

`security.py` 使用以下不可变返回类型：

```python
@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: UUID
    role: str
    token_id: UUID
    expires_at: datetime


@dataclass(frozen=True)
class RefreshTokenParts:
    session_id: UUID
    secret: str
    raw: str


class TokenValidationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
```

密码使用 `PasswordHash.recommended()`；JWT 解码显式传入 `[settings.jwt_algorithm]`、issuer 和 audience；Refresh Token 使用 `secrets.token_urlsafe(32)`，格式固定为 `{session_uuid}.{secret}`，哈希使用 SHA-256。

- [ ] **Step 4: 运行安全测试和静态检查**

Run:

```powershell
uv run pytest tests/unit/test_security.py tests/unit/test_config.py -q
uv run ruff check app/core tests/unit/test_security.py tests/unit/test_config.py
uv run ruff format --check app/core tests/unit/test_security.py tests/unit/test_config.py
```

Expected: 测试 PASS；Ruff 两项退出码 0。

- [ ] **Step 5: 提交安全基础**

```powershell
git add backend/pyproject.toml backend/uv.lock backend/app/core/config.py backend/app/core/security.py backend/tests/unit/test_config.py backend/tests/unit/test_security.py
git commit -m "feat: 添加认证安全基础"
```

---

### Task 2: 用户、刷新会话、迁移与安全重置

**Files:**
- Create: `backend/app/db/models/user.py`
- Create: `backend/app/db/models/refresh_session.py`
- Modify: `backend/app/db/models/knowledge_base.py`
- Modify: `backend/app/db/models/__init__.py`
- Create: `backend/migrations/versions/20260713_03_auth_rbac.py`
- Create: `backend/scripts/reset_development_data.py`
- Modify: `backend/tests/unit/test_database_metadata.py`
- Create: `backend/tests/unit/test_reset_development_data.py`
- Modify: `backend/tests/integration/test_database_schema.py`

**Interfaces:**
- Consumes: Task 1 `hash_refresh_secret`。
- Produces: `UserRole = Literal["admin", "user"]` 与 `ADMIN_ROLE`、`USER_ROLE`。
- Produces: `User`、`RefreshSession` SQLAlchemy 模型。
- Produces: 非空外键 `KnowledgeBase.owner_id`。
- Produces: `python -m scripts.reset_development_data --yes`，只允许本地 development 数据库。

- [ ] **Step 1: 写模型和重置保护的失败测试**

更新元数据测试，断言表集合包含 `users`、`refresh_sessions`，并断言：

```python
assert KnowledgeBase.__table__.c.owner_id.nullable is False
assert len(KnowledgeBase.__table__.c.owner_id.foreign_keys) == 1
assert User.__table__.c.username.unique is True
assert RefreshSession.__table__.c.token_hash.unique is True
```

在重置脚本测试中把 production、远程主机和缺少 `--yes` 分别传给纯函数：

```python
def test_reset_guard_only_accepts_confirmed_local_development() -> None:
    assert validate_reset_target("development", "localhost", confirmed=True) is None
    with pytest.raises(RuntimeError, match="仅允许 development"):
        validate_reset_target("production", "localhost", confirmed=True)
    with pytest.raises(RuntimeError, match="仅允许本地数据库"):
        validate_reset_target("development", "db.example.com", confirmed=True)
    with pytest.raises(RuntimeError, match="必须显式确认"):
        validate_reset_target("development", "localhost", confirmed=False)
```

- [ ] **Step 2: 运行测试确认预期失败**

Run: `uv run pytest tests/unit/test_database_metadata.py tests/unit/test_reset_development_data.py -q`

Expected: FAIL，原因是新模型和脚本不存在。

- [ ] **Step 3: 实现模型与迁移**

`User` 使用 UUID 主键、规范化用户名、密码哈希、角色、启用状态和 `TimestampMixin`。`RefreshSession` 使用 UUID 主键、用户外键、唯一哈希、到期/撤销/替换字段。

迁移 `20260713_03` 必须按以下顺序：

1. 创建 `users`；
2. 创建 `refresh_sessions` 及 `user_id`、`expires_at` 索引；
3. 检测 `knowledge_bases.owner_id IS NULL` 的记录；存在时抛出明确异常，不删除数据；
4. 为 `knowledge_bases.owner_id` 添加用户外键和索引；
5. 把 `owner_id` 改为非空；
6. downgrade 先恢复可空并删除外键/索引，再删除新表。

`reset_development_data.py` 解析 `database_url` 后只接受 `APP_ENV=development` 且主机为 `localhost`、`127.0.0.1` 或 `::1`。确认后执行：

```sql
TRUNCATE TABLE knowledge_bases CASCADE
```

脚本不执行迁移，不删除表，不读取生产凭据。

- [ ] **Step 4: 显式清理当前本地开发数据并升级迁移**

先记录并停止阶段 2A 启动的本地前端和 FastAPI 进程，不停止 PostgreSQL，也不结束来源不明的进程。再输出并人工核对目标，确认 `APP_ENV=development`，数据库主机是本机，数据库名是当前项目开发库。然后执行：

```powershell
uv run python -m scripts.reset_development_data --yes
uv run alembic upgrade head
```

Expected: 旧知识库数据被显式清理；迁移创建 `users`、`refresh_sessions`，`knowledge_bases.owner_id` 变为非空外键。若目标不是本地 development 数据库，脚本必须拒绝执行。

- [ ] **Step 5: 运行模型、脚本与迁移测试**

Run:

```powershell
uv run pytest tests/unit/test_database_metadata.py tests/unit/test_reset_development_data.py -q
$env:RUN_DATABASE_TESTS='1'; uv run pytest tests/integration/test_database_schema.py -q; Remove-Item Env:RUN_DATABASE_TESTS
uv run ruff check app/db migrations scripts tests/unit/test_database_metadata.py tests/unit/test_reset_development_data.py
```

Expected: 单元测试 PASS；迁移后的本地测试库包含 6 张核心表和非空 owner 外键；Ruff 退出码 0。

- [ ] **Step 6: 提交数据模型**

```powershell
git add backend/app/db backend/migrations/versions/20260713_03_auth_rbac.py backend/scripts/reset_development_data.py backend/tests/unit/test_database_metadata.py backend/tests/unit/test_reset_development_data.py backend/tests/integration/test_database_schema.py
git commit -m "feat: 添加用户与刷新会话模型"
```

---

### Task 3: 登录、刷新、退出和当前用户接口

**Files:**
- Create: `backend/app/auth/__init__.py`
- Create: `backend/app/auth/schemas.py`
- Create: `backend/app/auth/service.py`
- Create: `backend/app/api/auth_dependencies.py`
- Create: `backend/app/api/v1/auth.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/unit/test_auth_service.py`
- Create: `backend/tests/integration/test_auth_api.py`

**Interfaces:**
- Consumes: Task 1 安全函数；Task 2 `User`、`RefreshSession`。
- Produces: `CurrentUserResponse`、`AuthSessionResponse`。
- Produces: `get_current_user(...) -> User`、`require_admin(...) -> User`。
- Produces: `/api/v1/auth/login|refresh|logout|me`。

- [ ] **Step 1: 写认证服务和 API 失败测试**

服务测试必须覆盖：正确登录、错误用户名仍调用虚拟校验、错误密码、停用账号统一为 `INVALID_CREDENTIALS`、Refresh 轮换、旧 Token 重放失败、退出撤销。

API 测试至少包含：

```python
response = await client.post(
    "/api/v1/auth/login",
    json={"username": "admin", "password": "correct horse battery"},
)
assert response.status_code == 200
assert response.json()["token_type"] == "bearer"
assert response.json()["user"]["role"] == "admin"
cookie = response.headers["set-cookie"]
assert "HttpOnly" in cookie
assert "Path=/api/v1/auth" in cookie

me = await client.get(
    "/api/v1/auth/me",
    headers={"Authorization": f"Bearer {response.json()['access_token']}"},
)
assert me.status_code == 200
assert me.json()["username"] == "admin"
```

再断言 Refresh 缺少可信 Origin 返回 `INVALID_ORIGIN`，合法 Origin 轮换 Cookie，Logout 清除 Cookie。

- [ ] **Step 2: 运行测试确认预期失败**

Run: `$env:RUN_DATABASE_TESTS='1'; uv run pytest tests/unit/test_auth_service.py tests/integration/test_auth_api.py -q; Remove-Item Env:RUN_DATABASE_TESTS`

Expected: FAIL，原因是认证服务和路由不存在。

- [ ] **Step 3: 实现认证服务与依赖**

响应契约固定为：

```python
class CurrentUserResponse(BaseModel):
    id: UUID
    username: str
    role: Literal["admin", "user"]
    is_active: bool


class AuthSessionResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: CurrentUserResponse
```

`AuthService` 提供以下方法：

```python
async def login(self, username: str, password: str) -> IssuedSession
async def refresh(self, raw_refresh_token: str) -> IssuedSession
async def logout(self, raw_refresh_token: str | None) -> None
async def revoke_all_for_user(self, user_id: UUID) -> None
```

Refresh 在同一数据库事务中锁定会话行、验证哈希和到期时间、撤销旧行并插入新行。发现旧 Token 被重复使用时返回 `TOKEN_REVOKED`，且不签发新令牌。

`get_current_user` 使用 `HTTPBearer(auto_error=False)`，解码 JWT 后查询用户；不存在或停用返回 401。`require_admin` 对非管理员返回 403。

登录和 Refresh 设置 Cookie，Logout 使用相同 Path 删除 Cookie。合法 Origin 来自 `Settings.trusted_origins`。

- [ ] **Step 4: 运行认证测试和回归测试**

Run:

```powershell
$env:RUN_DATABASE_TESTS='1'; uv run pytest tests/unit/test_auth_service.py tests/integration/test_auth_api.py -q; Remove-Item Env:RUN_DATABASE_TESTS
uv run pytest tests/unit/test_health_api.py tests/unit/test_ready_api.py -q
uv run ruff check app/auth app/api tests/unit/test_auth_service.py tests/integration/test_auth_api.py
```

Expected: 认证测试 PASS；健康检查仍匿名 PASS；Ruff 退出码 0。

- [ ] **Step 5: 提交认证 API**

```powershell
git add backend/app/auth backend/app/api/auth_dependencies.py backend/app/api/v1/auth.py backend/app/main.py backend/tests/unit/test_auth_service.py backend/tests/integration/test_auth_api.py
git commit -m "feat: 添加登录与刷新会话接口"
```

---

### Task 4: 管理员用户管理与首个管理员命令

**Files:**
- Create: `backend/app/api/v1/admin_users.py`
- Modify: `backend/app/main.py`
- Create: `backend/scripts/create_admin.py`
- Create: `backend/tests/integration/test_admin_users_api.py`
- Create: `backend/tests/unit/test_create_admin.py`

**Interfaces:**
- Consumes: Task 3 `require_admin`、`AuthService.revoke_all_for_user`。
- Produces: 管理员用户列表、创建、启停、角色切换和重置密码接口。
- Produces: `python -m scripts.create_admin --username admin`。

- [ ] **Step 1: 写管理员规则的失败测试**

覆盖以下结果：

```python
assert (await admin_client.get("/api/v1/admin/users")).status_code == 200
assert (await user_client.get("/api/v1/admin/users")).status_code == 403

created = await admin_client.post(
    "/api/v1/admin/users",
    json={"username": "alice", "password": "temporary pass 123", "role": "user"},
)
assert created.status_code == 201

duplicate = await admin_client.post(
    "/api/v1/admin/users",
    json={"username": "ALICE", "password": "temporary pass 456", "role": "user"},
)
assert duplicate.status_code == 409
assert duplicate.json()["error"]["code"] == "USERNAME_ALREADY_EXISTS"
```

另外测试：管理员不能停用自己；不能移除最后一个启用管理员；停用、降级和重置密码撤销目标用户全部 Refresh Session。

- [ ] **Step 2: 运行测试确认预期失败**

Run: `$env:RUN_DATABASE_TESTS='1'; uv run pytest tests/integration/test_admin_users_api.py tests/unit/test_create_admin.py -q; Remove-Item Env:RUN_DATABASE_TESTS`

Expected: FAIL，原因是管理员路由和初始化命令不存在。

- [ ] **Step 3: 实现管理员接口和初始化命令**

请求模型固定为：

```python
class AdminUserCreate(BaseModel):
    username: Annotated[str, StringConstraints(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9._-]+$")]
    password: Annotated[str, StringConstraints(min_length=12, max_length=128)]
    role: Literal["admin", "user"]


class AdminUserUpdate(BaseModel):
    role: Literal["admin", "user"] | None = None
    is_active: bool | None = None


class AdminPasswordReset(BaseModel):
    password: Annotated[str, StringConstraints(min_length=12, max_length=128)]
```

管理员列表响应固定增加审计时间：

```python
class AdminUserResponse(CurrentUserResponse):
    created_at: datetime
    updated_at: datetime
```

用户名写入前统一 `strip().lower()`。保护“最后一个启用管理员”的查询和更新放在同一事务内，并对管理员集合加行锁。

`create_admin.py`：

- 接受 `--username`；
- 密码优先读取 `INITIAL_ADMIN_PASSWORD`，否则使用 `getpass.getpass()` 两次确认；
- 拒绝覆盖已存在用户；
- 只输出管理员 ID 和用户名，不输出密码或哈希。

- [ ] **Step 4: 运行管理员测试**

Run:

```powershell
$env:RUN_DATABASE_TESTS='1'; uv run pytest tests/integration/test_admin_users_api.py tests/unit/test_create_admin.py -q; Remove-Item Env:RUN_DATABASE_TESTS
uv run ruff check app/api/v1/admin_users.py scripts/create_admin.py tests/integration/test_admin_users_api.py tests/unit/test_create_admin.py
```

Expected: 所有规则测试 PASS；Ruff 退出码 0。

- [ ] **Step 5: 提交管理员能力**

```powershell
git add backend/app/api/v1/admin_users.py backend/app/main.py backend/scripts/create_admin.py backend/tests/integration/test_admin_users_api.py backend/tests/unit/test_create_admin.py
git commit -m "feat: 添加管理员用户管理"
```

---

### Task 5: 知识库归属与列表隔离

**Files:**
- Create: `backend/app/authorization/__init__.py`
- Create: `backend/app/authorization/service.py`
- Modify: `backend/app/api/v1/knowledge_bases.py`
- Create: `backend/tests/integration/test_knowledge_base_permissions.py`
- Modify: `backend/tests/integration/test_document_ingestion_api.py`
- Modify: `backend/tests/integration/test_vector_ingestion.py`
- Modify: `backend/tests/integration/test_vector_retriever.py`

**Interfaces:**
- Consumes: Task 2 `KnowledgeBase.owner_id`；Task 3 `get_current_user`。
- Produces: `get_accessible_knowledge_base(session, current_user, knowledge_base_id, *, for_update=False) -> KnowledgeBase`。
- Produces: 知识库响应中的 `owner_id` 与 `owner_username`。

- [ ] **Step 1: 写知识库权限矩阵失败测试**

创建 admin、alice、bob 三个用户并断言：

```python
alice_create = await alice_client.post(
    "/api/v1/knowledge-bases",
    json={"name": "Alice KB", "description": "private"},
)
assert alice_create.status_code == 201
assert alice_create.json()["owner_username"] == "alice"

assert [item["name"] for item in (await bob_client.get("/api/v1/knowledge-bases")).json()] == []
assert [item["name"] for item in (await admin_client.get("/api/v1/knowledge-bases")).json()] == ["Alice KB"]
assert (await anonymous_client.get("/api/v1/knowledge-bases")).status_code == 401
```

再测试客户端提交伪造 `owner_id` 不会改变创建者，管理员创建知识库默认归属管理员。

- [ ] **Step 2: 运行测试确认预期失败**

Run: `$env:RUN_DATABASE_TESTS='1'; uv run pytest tests/integration/test_knowledge_base_permissions.py -q; Remove-Item Env:RUN_DATABASE_TESTS`

Expected: FAIL，现有接口匿名可用且没有 owner 信息。

- [ ] **Step 3: 实现统一知识库授权和路由过滤**

`get_accessible_knowledge_base` 查询规则固定为：

```python
statement = select(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id)
if current_user.role != ADMIN_ROLE:
    statement = statement.where(KnowledgeBase.owner_id == current_user.id)
if for_update:
    statement = statement.with_for_update()
knowledge_base = await session.scalar(statement)
if knowledge_base is None:
    raise AppError(code="KNOWLEDGE_BASE_NOT_FOUND", message="知识库不存在。", status_code=404)
return knowledge_base
```

创建接口强制 `owner_id=current_user.id`。列表接口普通用户按 owner 过滤，管理员不加 owner 条件，并 join `User.username` 返回所有者。

更新原数据库集成测试的知识库构造，让每条记录都有测试用户 owner；不得用 nullable owner 绕过新约束。

- [ ] **Step 4: 运行权限和现有入库回归**

Run:

```powershell
$env:RUN_DATABASE_TESTS='1'; uv run pytest tests/integration/test_knowledge_base_permissions.py tests/integration/test_document_ingestion_api.py tests/integration/test_vector_ingestion.py tests/integration/test_vector_retriever.py -q; Remove-Item Env:RUN_DATABASE_TESTS
uv run ruff check app/authorization app/api/v1/knowledge_bases.py tests/integration
```

Expected: Alice/Bob 隔离、管理员全局可见，现有向量入库测试通过。

- [ ] **Step 5: 提交知识库隔离**

```powershell
git add backend/app/authorization/__init__.py backend/app/authorization/service.py backend/app/api/v1/knowledge_bases.py backend/tests/integration/test_knowledge_base_permissions.py backend/tests/integration/test_document_ingestion_api.py backend/tests/integration/test_vector_ingestion.py backend/tests/integration/test_vector_retriever.py
git commit -m "feat: 隔离用户知识库"
```

---

### Task 6: 文档、重处理和问答权限隔离

**Files:**
- Modify: `backend/app/authorization/service.py`
- Modify: `backend/app/api/v1/documents.py`
- Modify: `backend/app/api/v1/questions.py`
- Create: `backend/tests/integration/test_resource_permissions.py`
- Modify: `backend/tests/integration/test_document_reprocess_api.py`
- Modify: `backend/tests/integration/test_question_api.py`
- Modify: `backend/tests/unit/test_rag_service.py`

**Interfaces:**
- Consumes: Task 5 `get_accessible_knowledge_base`。
- Produces: `get_accessible_document(session, current_user, document_id, *, for_update=False) -> Document`。
- Produces: 所有文档、重处理和问答路由的统一 owner 检查。

- [ ] **Step 1: 写跨用户资源访问失败测试**

Alice 创建知识库并上传文档后，断言：

```python
assert (await bob_client.get(f"/api/v1/documents/{document_id}")).status_code == 404
assert (await bob_client.post(f"/api/v1/documents/{document_id}/reprocess")).status_code == 404
assert (
    await bob_client.post(
        f"/api/v1/knowledge-bases/{alice_kb_id}/questions",
        json={"question": "年假有几天？"},
    )
).status_code == 404
assert (await admin_client.get(f"/api/v1/documents/{document_id}")).status_code == 200
```

同时断言伪造不存在 UUID 和他人 UUID 都返回相同 `KNOWLEDGE_BASE_NOT_FOUND` 或 `DOCUMENT_NOT_FOUND`，不能泄露资源存在性。

- [ ] **Step 2: 运行测试确认预期失败**

Run: `$env:RUN_DATABASE_TESTS='1'; uv run pytest tests/integration/test_resource_permissions.py -q; Remove-Item Env:RUN_DATABASE_TESTS`

Expected: FAIL，现有文档状态和问答路由缺少用户身份检查。

- [ ] **Step 3: 在业务操作前执行统一授权**

上传接口先调用 `get_accessible_knowledge_base`，再读取文件和写磁盘。这样无权限请求不能通过文件大小或重复哈希侧信道探测资源。

文档查询和重处理使用 `get_accessible_document`，其 SQL join `Document -> KnowledgeBase` 并按普通用户 owner 过滤；重处理传 `for_update=True`。

问答路由在调用 `RagService.answer` 前先调用 `get_accessible_knowledge_base`。`RagService` 保留自己的知识库存在性防御检查，但不负责用户授权。

- [ ] **Step 4: 运行全部后端测试**

Run:

```powershell
uv run pytest -q
uv run ruff check app tests migrations scripts
uv run ruff format --check app tests migrations scripts
```

Expected: 默认测试全部通过，数据库测试按开关跳过；Ruff 两项退出码 0。

Run database suite:

```powershell
$env:RUN_DATABASE_TESTS='1'; uv run pytest tests/integration -q; Remove-Item Env:RUN_DATABASE_TESTS
```

Expected: 权限矩阵和原有文档/问答集成测试全部通过。

- [ ] **Step 5: 提交资源权限**

```powershell
git add backend/app/authorization/service.py backend/app/api/v1/documents.py backend/app/api/v1/questions.py backend/tests/integration/test_resource_permissions.py backend/tests/integration/test_document_reprocess_api.py backend/tests/integration/test_question_api.py backend/tests/unit/test_rag_service.py
git commit -m "feat: 保护文档与问答资源"
```

---

### Task 7: 前端认证 API、Store 与单次刷新协调

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `frontend/src/types/api.ts`
- Create: `frontend/src/api/auth.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/api/client.spec.ts`
- Create: `frontend/src/api/auth.spec.ts`
- Create: `frontend/src/stores/auth.ts`
- Create: `frontend/src/stores/auth.spec.ts`
- Modify: `frontend/src/stores/workspace.ts`
- Modify: `frontend/src/stores/workspace.spec.ts`

**Interfaces:**
- Consumes: Task 3 `AuthSessionResponse` 和错误契约。
- Produces: `UserRole`、`CurrentUser`、`AuthSession` TypeScript 类型。
- Produces: `useAuthStore()` 的 `initialize`、`login`、`logout`。
- Produces: API Client 的 Bearer 注入、共享 Refresh Promise 和最多一次重放。

- [ ] **Step 1: 安装 Vue Router 并写失败测试**

Run: `npm.cmd install vue-router@^4`

Client 测试固定覆盖两个并发 401 只调用一次刷新：

```typescript
const refresh = vi.fn().mockResolvedValue('new-token')
configureAuthentication({
  getAccessToken: () => 'old-token',
  refreshAccessToken: refresh,
  onAuthenticationFailed: vi.fn(),
})
fetchMock
  .mockResolvedValueOnce(new Response('{}', { status: 401 }))
  .mockResolvedValueOnce(new Response('{}', { status: 401 }))
  .mockResolvedValue(new Response('{"ok":true}', {
    status: 200, headers: { 'Content-Type': 'application/json' },
  }))
await Promise.all([apiRequest('/one'), apiRequest('/two')])
expect(refresh).toHaveBeenCalledOnce()
```

Store 测试覆盖：初始化 Refresh 成功、登录保存 Access Token、退出清空 auth 和 workspace、Refresh 失败保持匿名。

- [ ] **Step 2: 运行测试确认预期失败**

Run: `npm.cmd run test -- --run src/api/client.spec.ts src/api/auth.spec.ts src/stores/auth.spec.ts src/stores/workspace.spec.ts`

Expected: FAIL，认证模块和配置接口不存在。

- [ ] **Step 3: 实现认证 API 和内存状态**

类型固定为：

```typescript
export type UserRole = 'admin' | 'user'
export interface CurrentUser {
  id: string
  username: string
  role: UserRole
  is_active: boolean
}
export interface AuthSession {
  access_token: string
  token_type: 'bearer'
  expires_in: number
  user: CurrentUser
}
export interface AdminUser extends CurrentUser {
  created_at: string
  updated_at: string
}
```

同时把现有知识库类型扩展为：

```typescript
export interface KnowledgeBase {
  id: string
  name: string
  description: string | null
  owner_id: string
  owner_username: string
}
```

`auth.ts` 的 login/refresh/logout 全部使用 `credentials: 'include'`；login 和 refresh 禁用 401 自动重试。

Client 暴露：

```typescript
export interface AuthenticationCallbacks {
  getAccessToken: () => string | null
  refreshAccessToken: () => Promise<string | null>
  onAuthenticationFailed: () => void
}

export function configureAuthentication(callbacks: AuthenticationCallbacks): void
export async function apiRequest<T>(
  path: string,
  init?: RequestInit,
  options?: { authenticated?: boolean; retryUnauthorized?: boolean },
): Promise<T>
```

`useAuthStore` 初始化时注册 callbacks。刷新失败只执行一次 `onAuthenticationFailed`。`logout` 无论 API 是否成功都清空 Access Token、用户和 `workspace.reset()`。

Store 状态名称固定为：

```typescript
const accessToken = ref<string | null>(null)
const user = ref<CurrentUser | null>(null)
const initialized = ref(false)
const initializing = ref(false)
const isAdmin = computed(() => user.value?.role === 'admin')
```

Workspace 新增：

```typescript
function reset() {
  knowledgeBases.value = []
  activeKnowledgeBaseId.value = null
  documents.value = {}
  answer.value = null
  asking.value = false
}
```

- [ ] **Step 4: 运行认证内核测试和类型检查**

Run:

```powershell
npm.cmd run test -- --run src/api/client.spec.ts src/api/auth.spec.ts src/stores/auth.spec.ts src/stores/workspace.spec.ts
npm.cmd run type-check
```

Expected: 测试 PASS；TypeScript 退出码 0。

- [ ] **Step 5: 提交前端认证内核**

```powershell
git add frontend/package.json frontend/package-lock.json frontend/src/types/api.ts frontend/src/api/client.ts frontend/src/api/client.spec.ts frontend/src/api/auth.ts frontend/src/api/auth.spec.ts frontend/src/stores/auth.ts frontend/src/stores/auth.spec.ts frontend/src/stores/workspace.ts frontend/src/stores/workspace.spec.ts
git commit -m "feat: 添加前端认证状态"
```

---

### Task 8: 登录页、路由守卫与用户头部

**Files:**
- Create: `frontend/src/router/index.ts`
- Create: `frontend/src/router/index.spec.ts`
- Create: `frontend/src/views/LoginView.vue`
- Create: `frontend/src/views/LoginView.spec.ts`
- Create: `frontend/src/views/WorkspaceView.vue`
- Create: `frontend/src/views/AdminUsersView.vue`
- Create: `frontend/src/views/ForbiddenView.vue`
- Create: `frontend/src/components/AppHeader.vue`
- Create: `frontend/src/components/AppHeader.spec.ts`
- Modify: `frontend/src/components/KnowledgeBaseSidebar.vue`
- Modify: `frontend/src/components/KnowledgeBaseSidebar.spec.ts`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/App.spec.ts`
- Modify: `frontend/src/main.ts`
- Modify: `frontend/src/styles/main.css`

**Interfaces:**
- Consumes: Task 7 `useAuthStore`。
- Produces: `/login`、`/`、`/admin/users`、`/forbidden` 路由和守卫。
- Produces: 登录页面和用户头部。

- [ ] **Step 1: 写路由、登录和头部失败测试**

路由测试断言：

```typescript
await router.push('/')
await router.isReady()
expect(router.currentRoute.value.fullPath).toBe('/login')

auth.user = { id: 'u1', username: 'alice', role: 'user', is_active: true }
await router.push('/admin/users')
expect(router.currentRoute.value.fullPath).toBe('/forbidden')
```

登录页测试填写唯一的用户名、密码输入框，点击“登录”，成功后跳转 `/`；失败时显示 `formatApiError`。头部测试普通用户无管理员入口，管理员有“用户管理”，退出按钮调用 store。侧栏测试管理员能看到 `owner_username`，普通用户不渲染所有者副标题。

- [ ] **Step 2: 运行测试确认预期失败**

Run: `npm.cmd run test -- --run src/router/index.spec.ts src/views/LoginView.spec.ts src/components/AppHeader.spec.ts src/App.spec.ts`

Expected: FAIL，路由和页面不存在。

- [ ] **Step 3: 实现页面壳与守卫**

路由 meta 固定为：

```typescript
const routes = [
  { path: '/login', component: LoginView, meta: { public: true } },
  { path: '/', component: WorkspaceView, meta: { requiresAuth: true } },
  { path: '/admin/users', component: AdminUsersView, meta: { requiresAuth: true, admin: true } },
  { path: '/forbidden', component: ForbiddenView, meta: { requiresAuth: true } },
]
```

守卫第一次导航先 `await auth.initialize()`。匿名用户进入受保护路由跳登录；已登录用户进入 `/login` 跳 `/`；普通用户进入 admin 跳 `/forbidden`。

把现有 App 工作区内容原样移动到 `WorkspaceView.vue`，不改变上传、轮询和问答行为。任务 8 先创建可独立构建的管理员路由页：

```vue
<template>
  <section class="admin-users-page workspace-card">
    <h2>用户管理</h2>
  </section>
</template>
```

`App.vue` 只渲染 `AppHeader` 与 `RouterView`。登录页不显示用户头部。

`KnowledgeBaseSidebar` 从 auth store 读取 `isAdmin`。管理员在每个知识库名称下看到 `owner_username`，普通用户不显示所有者副标题。

- [ ] **Step 4: 运行页面测试、全量测试和构建**

Run:

```powershell
npm.cmd run test -- --run
npm.cmd run type-check
npm.cmd run build
```

Expected: 全部前端测试 PASS；类型检查和生产构建退出码 0。

- [ ] **Step 5: 提交登录与路由**

```powershell
git add frontend/src/router/index.ts frontend/src/router/index.spec.ts frontend/src/views/LoginView.vue frontend/src/views/LoginView.spec.ts frontend/src/views/WorkspaceView.vue frontend/src/views/AdminUsersView.vue frontend/src/views/ForbiddenView.vue frontend/src/components/AppHeader.vue frontend/src/components/AppHeader.spec.ts frontend/src/components/KnowledgeBaseSidebar.vue frontend/src/components/KnowledgeBaseSidebar.spec.ts frontend/src/App.vue frontend/src/App.spec.ts frontend/src/main.ts frontend/src/styles/main.css
git commit -m "feat: 添加登录与受保护路由"
```

---

### Task 9: 管理员用户管理页面与移动端布局

**Files:**
- Create: `frontend/src/api/adminUsers.ts`
- Create: `frontend/src/api/adminUsers.spec.ts`
- Create: `frontend/src/stores/adminUsers.ts`
- Create: `frontend/src/stores/adminUsers.spec.ts`
- Modify: `frontend/src/views/AdminUsersView.vue`
- Create: `frontend/src/views/AdminUsersView.spec.ts`
- Modify: `frontend/src/styles/main.css`

**Interfaces:**
- Consumes: Task 4 管理员用户接口；Task 8 `/admin/users` 路由。
- Produces: 用户列表、创建、启停、角色切换和重置密码 UI。

- [ ] **Step 1: 写管理员页面失败测试**

Store 测试 mock API 并断言每次 mutation 成功后只更新对应用户，不重复清空整个列表。

页面测试覆盖：

```typescript
expect(wrapper.text()).toContain('用户管理')
await wrapper.get('[data-test="create-user"]').trigger('click')
await wrapper.get('[data-test="username"]').setValue('alice')
await wrapper.get('[data-test="password"]').setValue('temporary pass 123')
await wrapper.get('[data-test="submit-user"]').trigger('click')
expect(store.createUser).toHaveBeenCalledWith({
  username: 'alice', password: 'temporary pass 123', role: 'user',
})
```

再覆盖停用确认、最后管理员错误显示、角色切换和重置密码。所有按钮使用稳定 `data-test`。

- [ ] **Step 2: 运行测试确认预期失败**

Run: `npm.cmd run test -- --run src/api/adminUsers.spec.ts src/stores/adminUsers.spec.ts src/views/AdminUsersView.spec.ts`

Expected: FAIL，管理员前端模块不存在。

- [ ] **Step 3: 实现管理员状态和页面**

API 输入类型固定为：

```typescript
export interface AdminUserCreateInput {
  username: string
  password: string
  role: UserRole
}
export interface AdminUserUpdateInput {
  role?: UserRole
  is_active?: boolean
}
```

页面使用 Element Plus 表格；用户名、角色、状态和操作列在桌面展示。480px 以下改为卡片列表或允许表格容器内部滚动，但页面根节点必须满足 `scrollWidth <= clientWidth`。密码输入不回显，成功后立即清空表单。

- [ ] **Step 4: 运行测试和 320px 浏览器检查**

Run:

```powershell
npm.cmd run test -- --run
npm.cmd run type-check
npm.cmd run build
```

Expected: 测试、类型检查、构建全部通过。

启动本地前端后用真实 Edge 设置 320×800，登录管理员并访问 `/admin/users`，执行：

```javascript
({ clientWidth: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth })
```

Expected: `scrollWidth <= clientWidth`，创建用户、状态和操作按钮可见或可在局部容器内操作。

- [ ] **Step 5: 提交管理员前端**

```powershell
git add frontend/src/api/adminUsers.ts frontend/src/api/adminUsers.spec.ts frontend/src/stores/adminUsers.ts frontend/src/stores/adminUsers.spec.ts frontend/src/views/AdminUsersView.vue frontend/src/views/AdminUsersView.spec.ts frontend/src/styles/main.css
git commit -m "feat: 添加管理员用户页面"
```

---

### Task 10: 本地初始化、真实权限验收与中文文档

**Files:**
- Modify: `backend/.env.example`
- Modify: `README.md`
- Modify: `frontend/README.md`
- Create: `docs/阶段2B验证与演示.md`
- Modify: `backend/tests/unit/test_smoke_test.py`
- Modify: `backend/scripts/smoke_test.py`

**Interfaces:**
- Consumes: Tasks 1–9 全部公开接口。
- Produces: 从旧本地测试库安全重置、迁移、创建管理员、启动和浏览器验收的中文步骤。
- Produces: 认证版 smoke test。

- [ ] **Step 1: 先更新 smoke test 的失败断言**

测试要求 smoke test：

- 从环境读取 `SMOKE_USERNAME`、`SMOKE_PASSWORD`；
- 先登录并保存 Cookie；
- 后续请求携带 Bearer Token；
- 验证 `/auth/me`；
- 最后退出；
- 输出中不包含密码、Access Token 或 Refresh Token。

Run: `uv run pytest tests/unit/test_smoke_test.py -q`

Expected: FAIL，现有 smoke test 没有登录步骤。

- [ ] **Step 2: 更新 smoke test 并运行单元测试**

实现固定调用顺序：`health -> ready -> login -> me -> create knowledge base -> upload -> poll -> question -> logout`。请求失败继续显示状态码、错误码和 request ID，不打印密钥或令牌。

Run: `uv run pytest tests/unit/test_smoke_test.py -q`

Expected: PASS。

- [ ] **Step 3: 写中文初始化与验收文档**

文档必须给出以下安全顺序：

```powershell
cd backend
$env:APP_ENV='development'
uv run python -m scripts.reset_development_data --yes
uv run alembic upgrade head
uv run python -m scripts.create_admin --username admin
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --loop app.core.event_loop:new_event_loop
```

另一个终端：

```powershell
cd frontend
npm.cmd install
npm.cmd run dev -- --host 127.0.0.1 --port 5173
```

说明生产环境必须提供随机 `JWT_SECRET_KEY`、开启 Secure Cookie、使用同域 HTTPS 反向代理，且不能公开 PostgreSQL、Vite 或 Uvicorn 内部端口。

- [ ] **Step 4: 执行当前本地开发库显式重置与迁移**

先打印并人工核对 `APP_ENV`、数据库主机和数据库名；仅当确认为当前本地 development 数据库时执行：

```powershell
cd backend
uv run python -m scripts.reset_development_data --yes
uv run alembic upgrade head
uv run python -m scripts.create_admin --username admin
```

Expected: 旧测试知识库清空，迁移成功，首个管理员创建成功。不得对远程、共享或 production 数据库执行。

- [ ] **Step 5: 执行完整自动验证**

Frontend:

```powershell
cd frontend
npm.cmd run test -- --run
npm.cmd run type-check
npm.cmd run build
```

Backend:

```powershell
cd backend
uv run pytest -q
$env:RUN_DATABASE_TESTS='1'; uv run pytest tests/integration -q; Remove-Item Env:RUN_DATABASE_TESTS
uv run ruff check app tests migrations scripts
uv run ruff format --check app tests migrations scripts
```

Expected: 前端测试、类型检查和构建通过；后端默认与数据库测试通过；Ruff 两项通过。

- [ ] **Step 6: 执行真实浏览器权限验收**

使用真实 Edge，通过 `http://127.0.0.1:5173` 完成：

1. 管理员登录并创建 `alice`、`bob`；
2. Alice 创建知识库、上传 `01-年假制度.txt` 并提问；
3. Bob 登录后看不到 Alice 知识库；
4. 管理员能看到 Alice 和 Bob 的知识库及所有者；
5. 管理员停用 Alice，Alice 后续请求立即失败并返回登录页；
6. 页面刷新通过 Refresh Cookie 恢复管理员会话；
7. 退出后刷新不再恢复；
8. 320×800 下登录、工作台和管理员页均无根页面横向溢出。

记录页面结果、关键状态码和 request ID；不能把密码或 Token 写入证据文件。

- [ ] **Step 7: 提交收尾文档和 smoke test**

```powershell
git add backend/.env.example backend/scripts/smoke_test.py backend/tests/unit/test_smoke_test.py README.md frontend/README.md docs/阶段2B验证与演示.md
git commit -m "docs: 添加阶段 2B 运行与验收说明"
```

---

## 阶段 2B 完成检查

- [ ] 密码只保存 Argon2id 哈希，JWT 固定校验算法、issuer 和 audience。
- [ ] Refresh Token 只保存哈希，支持轮换、重放拒绝、退出和批量撤销。
- [ ] 系统无公开注册，管理员可以创建、启停、切换角色和重置密码。
- [ ] 最后一个启用管理员受到保护，管理员不能停用自己。
- [ ] 未登录用户不能访问业务 API；健康检查和 OpenAPI 保持公开。
- [ ] Alice、Bob 的知识库、文档和问答互相隔离，管理员可以访问全部。
- [ ] 前端 Access Token 只在内存中，并发 401 只触发一次 Refresh。
- [ ] 页面刷新可以恢复会话，退出、停用和密码重置会使长期会话失效。
- [ ] 登录页、工作台和管理员页在 320px 下可操作且无根页面横向溢出。
- [ ] 本地数据清理必须显式确认，迁移不自动删除业务数据。
- [ ] 前后端全量测试、数据库集成测试、类型检查、构建和 Ruff 检查通过。
- [ ] 本阶段未加入公开注册、邮件、MFA、用户删除、审计、流式回答或聊天历史。

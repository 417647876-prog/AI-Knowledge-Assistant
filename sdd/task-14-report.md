# Task 14 实施报告：API、Worker、gateway 和开发数据库镜像

## 范围与结果

- 完成生产编排：单 PostgreSQL、migrate、单 API、单 Worker、gateway；只有 gateway 发布 `127.0.0.1:8080:80`。
- 完成独立开发数据库编排：仅启动带持久卷的 PostgreSQL，并保留 `5432:5432`。
- 完成后端/前端多阶段 Dockerfile、Nginx SSE 与 SPA 配置、环境变量样例，以及 `/ready` 的数据库迁移版本检查。
- 未实现 Task 15 或阶段 5 内容；未改动普通内容接口的 owner 隔离逻辑。

## TDD 记录

### RED

先新建 Compose/SSE/Worker 健康静态契约测试，并为 `/health`、`/ready` 扩展测试。首次执行：

```powershell
uv run pytest tests/unit/test_compose_contract.py tests/unit/test_worker_health.py tests/unit/test_health_api.py tests/unit/test_ready_api.py -q
```

失败于测试收集：`ImportError: cannot import name 'migrations_are_current'`。此时迁移版本就绪依赖、Docker 文件和完整 Compose 尚未实现，符合预期 RED。

### GREEN

实现后端非 root 镜像、前端 `npm ci` 多阶段镜像、Compose 服务/依赖/健康检查、Nginx SSE 配置和迁移版本检查；同时让已有 `/ready` 单测显式覆盖新增依赖，避免单测连接真实 PostgreSQL。

以下聚焦单测通过：

```powershell
uv run pytest tests/unit/test_compose_contract.py tests/unit/test_worker_health.py tests/unit/test_health_api.py tests/unit/test_ready_api.py -q
# 24 passed（Task 14 最终聚焦回归）
```

质量检查通过：

```powershell
uv run ruff check app/api/v1/health.py tests/unit/test_compose_contract.py tests/unit/test_worker_health.py tests/unit/test_health_api.py tests/unit/test_ready_api.py
git diff --check
```

## Docker 运行验收

2026-07-18 最终运行门已通过。排查确认早期 BuildKit I/O 失败来自 C 盘写满和 Docker VHDX 只读挂载，而不是仓库代码。只清理未使用的 BuildKit 缓存，并在完整 SHA-256 校验后将 Docker 数据盘迁移到 G 盘；构建期间用临时 `.wslconfig` 将 WSL 内存限制为 6 GiB、交换文件放到 D 盘，物理可用内存始终高于 2 GiB 门槛。

镜像顺序构建结果：

- `deploy-api:latest`：成功，镜像大小 8.76 GB；最终续建耗时 6 分 01 秒。
- `deploy-worker:latest`：成功，复用后端层，耗时 7.4 秒。
- `deploy-gateway:latest`：成功；`npm ci` 审计 0 vulnerabilities，`vue-tsc` 和 Vite 生产构建成功。Vite 仍有 1,185.96 kB 单 chunk 性能警告，不影响本 Task 运行门。

真实启动暴露出并修复了三项仅静态测试无法发现的问题：

1. 只读根文件系统下 `uv run` 会尝试写缓存并同步开发依赖，Compose 改为直接使用镜像 PATH 中的 `alembic`、`uvicorn` 和 `python`。
2. Nginx 模板覆盖默认配置后缺少静态目录 `root`，补充 `/usr/share/nginx/html`、`index.html` 和显式 JSON `/health`。
3. gateway 只连接 internal 网络时 Docker Desktop 不建立宿主端口转发，新增仅 gateway 使用的公共 bridge；gateway 与 API 仍只共同连接固定 `172.28.0.0/24` 内部网络，gateway 地址保持 `172.28.0.10`。

最终 `docker compose up -d` 验收证据：

```text
postgres.health=healthy
api.health=healthy
worker.health=healthy
gateway.health=healthy
migrate.exit=0
GET http://127.0.0.1:8080/health -> 200 {"status":"healthy"}
GET http://127.0.0.1:8080/api/ready -> 200 {"status":"ready"}
container api /ready -> 200 {"status":"ready"}
container worker health -> exit 0
```

端口绑定检查中，PostgreSQL、migrate、API、Worker 均为 `{}`；gateway 唯一绑定为 `127.0.0.1:8080 -> 80/tcp`。验收结束后执行了不带 `-v` 的 `docker compose down`，容器和网络均已移除，`deploy_knowledge_postgres_data`、`deploy_knowledge_uploads`、`deploy_knowledge_hf_cache` 三个卷保留。

## 限制与后续

- `CURRENT_MIGRATION_REVISION` 与当前迁移 head `20260716_08` 对齐；后续新增迁移时必须同步更新该常量及对应测试。
- 前端单 chunk 警告留待后续性能优化，不阻塞 Task 14。
- Task 15 负责更深的登录、上传、SSE、重启恢复和持久化验收，本报告不提前声称这些场景已通过。

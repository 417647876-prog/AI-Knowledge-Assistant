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
# 10 passed in 1.69s
```

质量检查通过：

```powershell
uv run ruff check app/api/v1/health.py tests/unit/test_compose_contract.py tests/unit/test_worker_health.py tests/unit/test_health_api.py tests/unit/test_ready_api.py
git diff --check
```

## Docker 验证

Docker 操作前只读检查资源：物理内存 `15.93 GiB`，D 盘可用 `13.05 GiB`，均大于 2 GiB。

两份编排均成功解析：

```powershell
docker compose -f deploy/docker-compose.yml config
docker compose -f deploy/docker-compose.dev.yml config
```

生产 Compose 解析结果确认：API、Worker、PostgreSQL 无宿主端口，唯一宿主端口为 gateway 的 `127.0.0.1:8080:80`；开发 Compose 仅有 PostgreSQL 的 `5432:5432`。

镜像构建命令已按要求执行：

```powershell
docker compose -f deploy/docker-compose.yml build api worker gateway
```

构建未能开始，原因是本机 Docker Desktop Linux 引擎未运行：

```text
open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified
```

因此未启动任何容器、未创建本次卷，也没有可停止的本次容器。没有执行 Task 15 的重启或持久化验收。

## 限制与后续

- Docker Desktop 启动后，应顺序重跑上述 build，再执行 `docker compose -f deploy/docker-compose.yml up -d` 验证 gateway 暴露、容器内 `/ready` 和 Worker health，最后 `docker compose -f deploy/docker-compose.yml down`（不带 `-v`，保留卷）。
- `CURRENT_MIGRATION_REVISION` 与当前迁移 head `20260716_08` 对齐；后续新增迁移时必须同步更新该常量及对应测试。

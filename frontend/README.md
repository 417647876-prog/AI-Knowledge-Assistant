# 阶段 2B 前端使用说明

前端使用 Vue 3、TypeScript、Vite、Pinia、Vue Router 和 Element Plus，通过 Vite 的同域 `/api` 代理访问本地 FastAPI。

## 本地启动

准备 Docker、`uv` 和 Node.js，并从仓库根目录打开两个 PowerShell 终端。

终端 1 启动 PostgreSQL、迁移、初始化管理员并启动后端：

```powershell
Set-Location (git rev-parse --show-toplevel)
docker compose -f deploy/docker-compose.yml up -d
Set-Location backend
$env:APP_ENV = "development"
uv sync --dev
uv run alembic upgrade head
uv run python -m scripts.create_admin --username admin
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --loop app.core.event_loop:new_event_loop
```

`create_admin` 默认安全提示两次输入密码。若使用当前进程环境变量，请在创建后立即执行 `Remove-Item Env:INITIAL_ADMIN_PASSWORD`，不要把密码写进脚本、仓库或报告。

终端 2 安装依赖并启动前端：

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location frontend
npm.cmd install
npm.cmd run dev -- --host 127.0.0.1 --port 5173
```

访问 <http://127.0.0.1:5173>。Swagger、健康和就绪检查分别位于 <http://127.0.0.1:8000/docs>、<http://127.0.0.1:8000/health> 和 <http://127.0.0.1:8000/ready>。

## 登录与权限

- 系统没有公开注册入口，账号由管理员创建。
- Access Token 只保存在 Pinia 内存中，通过 Bearer Header 发送。
- Refresh Token 只通过 HttpOnly Cookie 发送；页面刷新时用于恢复会话。
- 管理员可管理用户并查看所有知识库及所有者；普通用户只能查看和操作自己的知识库。
- 退出、账号停用或密码重置会撤销长期会话；认证失效后受保护页面会返回登录页。
- 工作台会在进入或切换知识库时重新加载历史文档；刷新页面后仍可看到此前上传的文档。
- `等待处理`、`解析中`、`向量化中` 的文档会自动恢复状态轮询；失败文档可重新处理。
- 删除文档需要确认；处理中任务不能删除，避免与后台处理竞争。删除成功后列表会立即移除该行。

## 阶段 2C：文档管理接口

文档列表接口为 `GET /api/v1/knowledge-bases/{knowledge_base_id}/documents`，返回 `items` 数组；每项包含 `document_id`、最新 `job_id`、`file_name`、`status`、`error_code` 和 `error_message`。

状态只可能是 `pending`、`parsing`、`embedding`、`ready`、`failed`。重新处理使用 `POST /api/v1/documents/{document_id}/reprocess`，删除使用 `DELETE /api/v1/documents/{document_id}`。后端会在同一事务中删除文档、任务和切片；文件清理失败会回滚数据库删除。处理中的文档删除返回 `409 DOCUMENT_PROCESSING`。

本阶段的详细设计见 [阶段 2C 文档管理计划](../docs/superpowers/plans/2026-07-14-stage-2c-document-management.md)，可重复执行的命令和浏览器步骤见 [阶段 2C 验证与演示](../docs/阶段2C验证与演示.md)。

## 同域部署与手机访问边界

生产环境应由同一个 HTTPS 域名提供前端静态文件，并把 `/api` 反向代理到内部 FastAPI。后端必须使用随机高强度 `JWT_SECRET_KEY`、`REFRESH_COOKIE_SECURE=true` 和精确的 `TRUSTED_ORIGINS`。PostgreSQL、Vite、Uvicorn 端口不能直接暴露公网。

默认命令只监听 `127.0.0.1`，因此手机不能远程访问。局域网访问需要额外评估绑定地址、防火墙、HTTPS 与可信 Origin；公网部署还需要正式的反向代理、证书、密钥管理和运维加固。本阶段文档不承诺开发服务器适合公网使用。

## 前端验证

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location frontend
npm.cmd run test -- --run
npm.cmd run type-check
npm.cmd run build
```

完整的数据库显式重置风险、管理员初始化、认证 smoke 和权限验收步骤见 [阶段 2B 验证与演示](../docs/阶段2B验证与演示.md)。

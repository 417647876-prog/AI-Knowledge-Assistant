# 阶段 2B 前端使用说明

前端使用 Vue 3、TypeScript、Vite、Pinia、Vue Router 和 Element Plus，通过 Vite 的同域 `/api` 代理访问本地 FastAPI。

## 本地启动

准备 Docker、`uv` 和 Node.js，并从仓库根目录打开两个 PowerShell 终端。

终端 1 启动 PostgreSQL、迁移、初始化管理员并启动后端：

```powershell
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
- 当前文档列表只保存在浏览器会话状态中，刷新页面后不会重新列出此前上传的文档。

## 同域部署与手机访问边界

生产环境应由同一个 HTTPS 域名提供前端静态文件，并把 `/api` 反向代理到内部 FastAPI。后端必须使用随机高强度 `JWT_SECRET_KEY`、`REFRESH_COOKIE_SECURE=true` 和精确的 `TRUSTED_ORIGINS`。PostgreSQL、Vite、Uvicorn 端口不能直接暴露公网。

默认命令只监听 `127.0.0.1`，因此手机不能远程访问。局域网访问需要额外评估绑定地址、防火墙、HTTPS 与可信 Origin；公网部署还需要正式的反向代理、证书、密钥管理和运维加固。本阶段文档不承诺开发服务器适合公网使用。

## 前端验证

```powershell
Set-Location frontend
npm.cmd run test -- --run
npm.cmd run type-check
npm.cmd run build
```

完整的数据库显式重置风险、管理员初始化、认证 smoke 和权限验收步骤见 [阶段 2B 验证与演示](../docs/阶段2B验证与演示.md)。

# 阶段 2A 前端使用说明

前端使用 Vue 3、TypeScript、Vite、Pinia 和 Element Plus，通过 Vite 开发服务器代理访问本地 FastAPI。

## 启动前准备

- 已安装 Docker、Python 项目工具 `uv` 和 Node.js。
- 以下命令都从仓库根目录开始执行。
- 首次运行前端时需要执行 `npm.cmd install`。

## 本地启动

打开两个 PowerShell 窗口。

终端 1：启动 PostgreSQL、执行数据库迁移并启动后端。

```powershell
docker compose -f deploy/docker-compose.yml up -d
Set-Location backend
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --loop app.core.event_loop:new_event_loop
```

终端 2：安装前端依赖并启动开发服务器。

```powershell
Set-Location frontend
npm.cmd install
npm.cmd run dev
```

启动后访问：

- 前端：<http://127.0.0.1:5173>
- Swagger：<http://127.0.0.1:8000/docs>

## 当前功能

可以创建和切换知识库，上传 TXT、Markdown、PDF、DOCX 或 XLSX 文档并观察处理状态，也可以向当前知识库提问并查看引用来源。

当前文档列表只保存在本次浏览器会话的前端状态中。刷新页面后，已创建的知识库仍会从后端重新加载，但此前上传的文档不会重新显示在列表中。

## 前端验证

```powershell
Set-Location frontend
npm.cmd run test -- --run
npm.cmd run type-check
npm.cmd run build
```

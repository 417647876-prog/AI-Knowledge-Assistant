# AI Knowledge Assistant

面向企业私有文档的 RAG 知识库后端，也是一个面向 C# 开发者的 Python AI 工程学习项目。

## 当前进度

- 阶段 1A：FastAPI 基础、统一错误、PostgreSQL + pgvector、Alembic。
- 阶段 1B：知识库 API、安全上传、PDF/DOCX/XLSX/Markdown/TXT 解析。
- 下一步：阶段 1C 文本清洗、切片、Embedding 与向量入库。

## 本地启动

```powershell
docker compose -f deploy/docker-compose.yml up -d
Set-Location backend
uv sync --dev
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --loop app.core.event_loop:new_event_loop
```

启动后访问：

- Swagger：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>
- 就绪检查：<http://127.0.0.1:8000/ready>

## 学习资料

- [项目学习笔记](docs/学习笔记.md)
- [第一阶段 RAG 后端设计](docs/superpowers/specs/2026-07-10-rag-backend-design.md)
- [阶段 1B 文档解析设计](docs/superpowers/specs/2026-07-12-stage-1b-knowledge-base-document-parsing-design.md)
- [学习任务与资源](MISSION.md)

## 验证

```powershell
Set-Location backend
uv run pytest -v
uv run ruff check app tests migrations
uv run ruff format --check app tests migrations
```

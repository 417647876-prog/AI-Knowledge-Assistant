# AI Knowledge Assistant

面向企业私有文档的 RAG 知识库后端，也是一个面向 C# 开发者的 Python AI 工程学习项目。

## 当前进度

- 阶段 1A：FastAPI 基础、统一错误、PostgreSQL + pgvector、Alembic。
- 阶段 1B：知识库 API、安全上传、PDF/DOCX/XLSX/Markdown/TXT 解析。
- 阶段 1C：文本清洗、中文递归切片、事务性向量入库。
- 阶段 1D：本地 BGE Small、pgvector 余弦检索、DeepSeek 问答与结构化引用。
- 阶段 1E：自动化测试、Docker 冒烟脚本、中文使用文档与演示材料。

第一阶段的 RAG 后端闭环已经完成：创建知识库 → 上传文档 → 解析和向量入库 → 检索问答 → 返回可追溯引用。

## 本地启动

```powershell
docker compose -f deploy/docker-compose.yml up -d
Set-Location backend
uv sync --dev
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --loop app.core.event_loop:new_event_loop
```

首次使用本地 Embedding 时会自动下载 `BAAI/bge-small-zh-v1.5`。DeepSeek Key 只填写在本地 `backend/.env`，不要提交到 Git。

启动后访问：

- Swagger：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>
- 就绪检查：<http://127.0.0.1:8000/ready>

## 阶段 2A 前端

阶段 2A 增加了基于 Vue 3、TypeScript、Vite、Pinia 和 Element Plus 的单页工作台，并使用 Vitest 进行前端测试。

从仓库根目录打开两个 PowerShell 窗口。终端 1 启动数据库和 FastAPI：

```powershell
docker compose -f deploy/docker-compose.yml up -d
Set-Location backend
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --loop app.core.event_loop:new_event_loop
```

终端 2 安装依赖并启动前端：

```powershell
Set-Location frontend
npm.cmd install
npm.cmd run dev
```

打开 <http://127.0.0.1:5173> 后，可以创建和切换知识库，上传 TXT、Markdown、PDF、DOCX 或 XLSX 文档并观察处理状态，以及提问并查看引用来源。当前文档列表只保存在本次浏览器会话中；刷新页面后不会重新加载此前上传的文档。

更完整的环境要求、访问地址和验证命令见 [阶段 2A 前端使用说明](frontend/README.md)。

## 离线冒烟验证

冒烟测试会实际调用已启动的 API，验证“创建知识库 → 上传 TXT → 后台入库 → 问答 → 引用”全链路。
为避免下载模型或调用付费模型，请另开一个 PowerShell 窗口，使用 Fake Provider 启动服务：

```powershell
Set-Location backend
$env:EMBEDDING_PROVIDER = "fake"
$env:CHAT_PROVIDER = "fake"
$env:RAG_SCORE_THRESHOLD = "-1"
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --loop app.core.event_loop:new_event_loop
```

待服务启动后，在第二个窗口执行：

```powershell
Set-Location backend
uv run python -m scripts.smoke_test
```

脚本只在本地 Docker 数据库中创建一条临时知识库和测试文档；它不读取 DeepSeek Key。

## 学习资料

- [项目学习笔记](docs/学习笔记.md)
- [第一阶段 RAG 后端设计](docs/superpowers/specs/2026-07-10-rag-backend-design.md)
- [阶段 1B 文档解析设计](docs/superpowers/specs/2026-07-12-stage-1b-knowledge-base-document-parsing-design.md)
- [阶段 1C 向量入库设计](docs/superpowers/specs/2026-07-13-stage-1c-chunking-vector-ingestion-design.md)
- [阶段 1D 本地语义检索与问答设计](docs/superpowers/specs/2026-07-13-stage-1d-retrieval-question-answering-design.md)
- [阶段 1E 验证与演示指南](docs/阶段1E验证与演示.md)
- [学习任务与资源](MISSION.md)

## 1D API

```http
POST /api/v1/knowledge-bases/{knowledge_base_id}/questions
Content-Type: application/json

{
  "question": "员工入职满一年有多少天年假？",
  "top_k": 5
}
```

向量维度从 1536 调整为 512 后，历史文档需要重新生成向量：

```http
POST /api/v1/documents/{document_id}/reprocess
```

## API 调用示例

下面以 PowerShell 为例，变量分别保存接口返回的知识库和文档 ID。上传后需要等待文档状态变为 `ready`，再发起提问。

```powershell
$knowledgeBase = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/knowledge-bases" `
  -ContentType "application/json" `
  -Body '{"name":"人事制度","description":"演示知识库"}'

$upload = curl.exe -s -X POST `
  -F "file=@./annual-leave.txt;type=text/plain" `
  "http://127.0.0.1:8000/api/v1/knowledge-bases/$($knowledgeBase.id)/documents" | ConvertFrom-Json

Invoke-RestMethod "http://127.0.0.1:8000/api/v1/documents/$($upload.document_id)"

Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/knowledge-bases/$($knowledgeBase.id)/questions" `
  -ContentType "application/json" `
  -Body '{"question":"员工入职满一年后有多少天年假？","top_k":5}'
```

## 验证

```powershell
Set-Location backend
uv run pytest -v
uv run ruff check app tests migrations
uv run ruff format --check app tests migrations
```

数据库集成测试需要先启动 Docker PostgreSQL：

```powershell
docker compose -f deploy/docker-compose.yml up -d
Set-Location backend
uv run alembic upgrade head
$env:RUN_DATABASE_TESTS = "1"
uv run pytest -v
```

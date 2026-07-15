# AI Knowledge Assistant

面向企业私有文档的 RAG 知识库后端，也是一个面向 C# 开发者的 Python AI 工程学习项目。

## 当前进度

- 阶段 1A：FastAPI 基础、统一错误、PostgreSQL + pgvector、Alembic。
- 阶段 1B：知识库 API、安全上传、PDF/DOCX/XLSX/Markdown/TXT 解析。
- 阶段 1C：文本清洗、中文递归切片、事务性向量入库。
- 阶段 1D：本地 BGE Small、pgvector 余弦检索、DeepSeek 问答与结构化引用。
- 阶段 1E：自动化测试、Docker 冒烟脚本、中文使用文档与演示材料。
- 阶段 2A：Vue 3 单页工作台、上传与问答界面。
- 阶段 2B：管理员账号、JWT 登录、可撤销刷新会话、角色权限和知识库隔离。
- 阶段 2C：持久化文档列表、处理状态恢复、失败重处理和安全删除。
- 阶段 2D：SSE 流式回答、浏览器会话历史、多轮问题改写和检索耗时事件。
- 阶段 3A：30 条中文评估数据、Recall/MRR/引用/拒答指标和纯向量基线 CLI。

当前闭环为：管理员初始化账号 → 用户登录 → 创建自己的知识库 → 上传文档 → 解析和向量入库 → 检索问答 → 返回可追溯引用。系统不提供公开注册，普通用户只能访问自己的资源，管理员可以查看和操作全部知识库。

## 阶段 3A 纯向量评估

阶段 3A 使用固定的 30 条中文案例评估关键词、语义、拒答、多轮和干扰问题。先准备已导入
`backend/tests/fixtures/documents/01-` 至 `05-` 测试资料的知识库，再执行：

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location backend
$env:EVALUATION_KNOWLEDGE_BASE_ID = "知识库 UUID"
uv run python -m scripts.evaluate_rag `
  --dataset tests/fixtures/evaluation/stage3.jsonl `
  --knowledge-base-id $env:EVALUATION_KNOWLEDGE_BASE_ID `
  --mode vector `
  --output reports/stage3a-vector-baseline.json
Remove-Item Env:EVALUATION_KNOWLEDGE_BASE_ID
```

报告记录 Recall@5、MRR@5、引用命中率、拒答准确率和检索延迟，但不会记录数据库连接串或
API Key。`backend/reports/*.json` 是本地运行产物，默认不提交 Git；执行状态和基线指标见
[阶段 3 执行进度](docs/阶段3执行进度.md)。

## 阶段 3B 混合检索状态

阶段 3B 已实现确定性中文 Token、PostgreSQL `tsvector` 与 GIN 索引、关键词 Retriever、
RRF 融合，以及 `vector`/`hybrid` 可回退配置。默认仍使用纯向量检索；显式设置
`RAG_RETRIEVAL_MODE=hybrid` 才启用混合检索。两个数据库 Retriever 共用请求级
`AsyncSession`，因此采用顺序双路查询后再融合，避免同一会话并发执行 SQL。

固定 30 条数据的本地验收结果如下：

| 模式 | Recall@5 | MRR@5 | 引用命中率 | 拒答准确率 |
|---|---:|---:|---:|---:|
| vector | 83.33% | 83.33% | 83.33% | 83.33% |
| hybrid | 93.33% | 93.33% | 93.33% | 93.33% |

关键词分类的纯向量 Recall@5 已经是 100%，因此 3B 使用上限感知质量门：混合检索的关键词
Recall@5 必须达到 `min(100%, 纯向量关键词 Recall@5 + 10 个百分点)`，同时总体 Recall@5、
引用命中率和拒答准确率均不得低于纯向量。混合检索保持关键词 Recall@5 为 100%，四项总体
指标均由 83.33% 提升到 93.33%，阶段 3B 已通过验收。

## 阶段 3C 重排序验收状态

评估 CLI 的 `--mode rerank` 会使用 hybrid 检索取得候选，启用本地 BGE 重排序，并关闭
fallback，避免模型失败时静默生成非重排报告。固定 30 条数据的 CPU 实测使用
`candidate_k=20`、`BAAI/bge-reranker-base`：

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location backend
$env:EVALUATION_KNOWLEDGE_BASE_ID = "知识库 UUID"
$env:EMBEDDING_DEVICE = "cpu"
$env:RAG_RERANKER_DEVICE = "cpu"
uv run python -m scripts.evaluate_rag `
  --dataset tests/fixtures/evaluation/stage3.jsonl `
  --knowledge-base-id $env:EVALUATION_KNOWLEDGE_BASE_ID `
  --mode rerank `
  --output reports/stage3c-rerank.json
```

| 模式 | MRR@5 | 引用命中率 | CPU P50 | CPU P95 |
|---|---:|---:|---:|---:|
| 3B hybrid | 93.33% | 93.33% | 17.89 ms | 23.84 ms |
| 3C rerank | 93.33% | 93.33% | 17.42 ms | 29.39 ms |

MRR@5 相对提升为 0.00%，未达到至少 5% 的质量门；引用命中率未下降。28 条案例在 3B
已经排名第一，`refusal-03` 的单个误召回无法仅靠排序剔除，`multi-turn-06` 则没有候选可供
重排。因此 3C Task 5 尚未完成，3D 保持阻塞，不得据此结果继续执行。

## 本地启动

```powershell
Set-Location (git rev-parse --show-toplevel)
docker compose -f deploy/docker-compose.yml up -d
Set-Location backend
uv sync --dev
$env:APP_ENV = "development"
uv run alembic upgrade head
uv run python -m scripts.create_admin --username admin
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --loop app.core.event_loop:new_event_loop
```

`create_admin` 会在终端中安全提示两次输入密码；也可仅在当前 PowerShell 进程设置 `INITIAL_ADMIN_PASSWORD`，执行后立即删除该环境变量。密码和 DeepSeek Key 只放在本地环境中，不要写入仓库、命令示例或验收报告。

另开一个 PowerShell 终端启动前端：

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location frontend
npm.cmd install
npm.cmd run dev -- --host 127.0.0.1 --port 5173
```

首次使用本地 Embedding 时会自动下载 `BAAI/bge-small-zh-v1.5`。如只做离线验收，可按下文使用 Fake Provider。

启动后访问：

- Swagger：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>
- 就绪检查：<http://127.0.0.1:8000/ready>

## 阶段 2B 前端与认证

前端基于 Vue 3、TypeScript、Vite、Pinia、Vue Router 和 Element Plus。Access Token 只保存在页面内存中；长期 Refresh Token 只由浏览器通过 HttpOnly Cookie 发送。刷新页面时前端使用 Cookie 恢复会话，退出、账号停用或密码重置会撤销长期会话。

从仓库根目录打开两个 PowerShell 窗口。终端 1 启动数据库和 FastAPI：

```powershell
Set-Location (git rev-parse --show-toplevel)
docker compose -f deploy/docker-compose.yml up -d
Set-Location backend
uv run alembic upgrade head
uv run python -m scripts.create_admin --username admin
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --loop app.core.event_loop:new_event_loop
```

终端 2 安装依赖并启动前端：

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location frontend
npm.cmd install
npm.cmd run dev -- --host 127.0.0.1 --port 5173
```

打开 <http://127.0.0.1:5173> 后先登录。管理员可在“用户管理”中创建、启停账号、切换角色和重置密码；普通用户只能看到自己的知识库。工作台会重新加载历史文档，并恢复处理中任务的状态轮询；失败文档可重新处理，删除会要求确认且不允许删除处理中任务。

更完整的初始化、安全重置、权限验收和验证命令见 [阶段 2B 验证与演示](docs/阶段2B验证与演示.md)、[前端使用说明](frontend/README.md)、[阶段 2C 文档管理计划](docs/superpowers/plans/2026-07-14-stage-2c-document-management.md) 和 [阶段 2C 验证与演示](docs/阶段2C验证与演示.md)。

## 离线冒烟验证

冒烟测试会实际调用已启动的 API，验证“创建知识库 → 上传 TXT → 后台入库 → 问答 → 引用”全链路。
为避免下载模型或调用付费模型，请另开一个 PowerShell 窗口，使用 Fake Provider 启动服务：

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location backend
$env:EMBEDDING_PROVIDER = "fake"
$env:CHAT_PROVIDER = "fake"
$env:RAG_SCORE_THRESHOLD = "-1"
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --loop app.core.event_loop:new_event_loop
```

待服务启动后，在第二个窗口执行：

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location backend
$env:SMOKE_USERNAME = "admin"
$securePassword = Read-Host "冒烟测试账号密码" -AsSecureString
$env:SMOKE_PASSWORD = [System.Net.NetworkCredential]::new("", $securePassword).Password
uv run python -m scripts.smoke_test
Remove-Item Env:SMOKE_PASSWORD
Remove-Variable securePassword
```

脚本先验证公开健康接口，再登录并验证 `/auth/me`，随后创建临时知识库、上传、轮询、问答，最后退出。账号密码只从当前进程环境读取；脚本不会输出密码、Access Token 或 Refresh Token。失败时返回非零退出码并显示脱敏的状态码、错误码和 request ID。

## 部署与手机访问边界

本阶段只验证本机开发运行，不代表已经具备公网生产安全。生产部署必须使用随机高强度 `JWT_SECRET_KEY`、`REFRESH_COOKIE_SECURE=true`，由同一 HTTPS 域名提供前端和 `/api` 反向代理，并严格配置 `TRUSTED_ORIGINS`。PostgreSQL、Vite 和 Uvicorn 内部端口不能直接暴露公网。

反向代理还必须提供两类入口保护：对登录接口按来源和账号维度做限速、限制并发连接；对文档上传设置真实请求体硬上限。例如 Nginx 的 `client_max_body_size` 应按“20 MB 文件 + multipart 编码开销”配置，不能只写 20 MB。应用内同时限制完整 multipart 请求体和文件内容大小，属于第二层防护；密码 128 字符上限和 Argon2 线程池隔离也不能替代代理层的抗 DoS、连接数与请求速率限制。

手机在同一局域网访问也需要额外绑定非回环地址、防火墙规则和可信 Origin；这些操作会扩大网络暴露面，不属于本阶段默认启动步骤。不要把开发命令直接用于公网。

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

## API 调用说明

除 `/health`、`/ready`、OpenAPI 和认证入口外，业务 API 都需要 `Authorization: Bearer <Access Token>`。普通用户直接访问他人的知识库、文档或问答资源返回 404；管理员接口要求管理员角色。完整且不在终端输出令牌的调用方式请使用前端或认证版 smoke 脚本。

## 验证

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location backend
uv run pytest -v
uv run ruff check app tests migrations scripts
uv run ruff format --check app tests migrations scripts
```

数据库集成测试会校验“最后一个管理员”和全局列表等约束，必须使用空的临时数据库，不能直接复用包含演示数据的开发库：

```powershell
Set-Location (git rev-parse --show-toplevel)
docker compose -f deploy/docker-compose.yml up -d
$testDatabase = "knowledge_integration_test"
docker compose -f deploy/docker-compose.yml exec -T postgres dropdb --if-exists --force -U knowledge $testDatabase
docker compose -f deploy/docker-compose.yml exec -T postgres createdb -U knowledge $testDatabase
try {
  $env:DATABASE_URL = "postgresql+psycopg://knowledge:knowledge@localhost:5432/$testDatabase"
  Set-Location backend
  uv run alembic upgrade head
  $env:RUN_DATABASE_TESTS = "1"
  uv run pytest tests/integration -q
}
finally {
  Remove-Item Env:RUN_DATABASE_TESTS -ErrorAction SilentlyContinue
  Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
  Set-Location (git rev-parse --show-toplevel)
  docker compose -f deploy/docker-compose.yml exec -T postgres dropdb --if-exists --force -U knowledge $testDatabase
}
```

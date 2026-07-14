# 阶段 1D 本地语义检索与问答 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使用本地 BGE Small、pgvector 和 DeepSeek 完成带真实引用的固定两阶段 RAG 问答闭环。

**Architecture:** `LocalEmbeddingProvider` 统一生成文档与问题向量，`VectorRetriever` 封装知识库隔离的余弦查询，`RagService` 固定编排 Retrieve 与 Generate。Prompt、Chat Provider 和引用映射保持独立，API 层只负责校验和 DTO 转换。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy Async、PostgreSQL、pgvector、sentence-transformers、httpx、Alembic、pytest、DeepSeek OpenAI 兼容接口。

## Global Constraints

- 所有用户文档和学习笔记使用中文。
- `BAAI/bge-small-zh-v1.5` 输出 512 维归一化向量。
- 每次检索必须使用 `knowledge_base_id` 过滤，默认 Top 5、最大 Top 20、默认阈值 0.55。
- 问答采用固定 `Retrieve → Generate`，不使用 Agent 自主工具调用。
- 自动化测试使用 Fake Provider，不下载模型、不访问外网、不产生费用。
- DeepSeek Key 只写入被 Git 忽略的本地 `.env`，不得进入源码、文档、日志或提交。
- 不实现 BM25、混合检索、Rerank、流式响应和前端。

---

### Task 1: 512 维迁移与配置契约

**Files:**
- Create: `backend/migrations/versions/20260713_02_embedding_512.py`
- Modify: `backend/app/db/models/document_chunk.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/.env.example`
- Test: `backend/tests/unit/test_config.py`
- Test: `backend/tests/integration/test_database_schema.py`

**Interfaces:**
- Produces: `Settings.embedding_provider: Literal["fake", "local", "openai"]`、本地模型与 Chat/RAG 配置。
- Produces: PostgreSQL `document_chunks.embedding vector(512)`。

- [ ] **Step 1: 写失败的配置和元数据测试**

```python
def test_local_embedding_and_rag_defaults(monkeypatch):
    monkeypatch.delenv("CHAT_API_KEY", raising=False)
    settings = Settings(_env_file=None)
    assert settings.embedding_dimensions == 512
    assert settings.embedding_model == "BAAI/bge-small-zh-v1.5"
    assert settings.embedding_device == "auto"
    assert settings.rag_top_k_default == 5
    assert settings.rag_score_threshold == 0.55

def test_document_chunk_uses_512_dimensions():
    assert DocumentChunk.__table__.c.embedding.type.dim == 512
```

- [ ] **Step 2: 运行测试确认因旧默认值失败**

Run: `cd backend; uv run pytest tests/unit/test_config.py tests/unit/test_database_metadata.py -q`

Expected: FAIL，显示维度仍为 1536 或缺少新配置。

- [ ] **Step 3: 实现配置和模型维度**

在 `Settings` 中加入：

```python
embedding_dimensions: int = Field(default=512, ge=512, le=512)
embedding_provider: Literal["fake", "local", "openai"] = "local"
embedding_model: str = "BAAI/bge-small-zh-v1.5"
embedding_device: Literal["auto", "cuda", "cpu"] = "auto"
chat_provider: Literal["fake", "deepseek"] = "fake"
chat_base_url: str = "https://api.deepseek.com"
chat_api_key: str | None = None
chat_model: str = "deepseek-v4-flash"
chat_timeout_seconds: float = Field(default=30.0, gt=0)
rag_top_k_default: int = Field(default=5, ge=1, le=20)
rag_top_k_max: int = Field(default=20, ge=1, le=100)
rag_score_threshold: float = Field(default=0.55, ge=-1.0, le=1.0)
rag_question_max_length: int = Field(default=2000, ge=1, le=10000)
```

Chat Provider 为 `deepseek` 时必须配置 Key；Top K 默认值不得超过最大值。模型列改为 `VECTOR(512)`。

- [ ] **Step 4: 新增 Alembic 迁移并验证**

迁移按顺序执行：清空 `document_chunks`、将现有文档状态重置为 `pending` 并清空错误、把向量列改为 `vector(512)`；downgrade 清空切片并改回 `vector(1536)`。

Run: `cd backend; $env:DATABASE_URL='postgresql+psycopg://knowledge:knowledge@localhost:5432/knowledge'; uv run alembic upgrade head`

Expected: 数据库升级到 `20260713_02`。

- [ ] **Step 5: 运行测试并提交**

Run: `cd backend; uv run pytest tests/unit/test_config.py tests/unit/test_database_metadata.py -q`

Expected: PASS。

Commit: `feat: 配置 512 维本地向量模型`

### Task 2: 本地 BGE Embedding Provider

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`
- Modify: `backend/app/ai/contracts.py`
- Modify: `backend/app/ai/embeddings.py`
- Test: `backend/tests/unit/test_embedding_providers.py`

**Interfaces:**
- Consumes: `embedding_dimensions=512`、`embedding_device`、`embedding_batch_size`。
- Produces: `EmbeddingProvider.embed_query(text: str) -> list[float]`。
- Produces: `LocalEmbeddingProvider(model_name, dimensions, batch_size, device, model_factory=None)`。

- [ ] **Step 1: 写失败的 Query 与本地模型测试**

使用 Fake 模型替身记录 `encode()` 参数，断言文档批处理与问题单条都设置 `normalize_embeddings=True`，输出都是 512 维；断言 Fake Provider 对同一文本的文档向量和问题向量一致。

- [ ] **Step 2: 运行测试确认契约缺失**

Run: `cd backend; uv run pytest tests/unit/test_embedding_providers.py -q`

Expected: FAIL，缺少 `embed_query` 和 `LocalEmbeddingProvider`。

- [ ] **Step 3: 实现最小 Provider**

```python
class EmbeddingProvider(Protocol):
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...
```

`LocalEmbeddingProvider` 使用 `asyncio.to_thread` 调用 sentence-transformers；模型按第一次调用延迟创建；`auto` 根据 `torch.cuda.is_available()` 选择设备；对结果执行数量和维度校验。新增依赖 `sentence-transformers>=5,<6`。

- [ ] **Step 4: 运行测试并提交**

Run: `cd backend; uv run pytest tests/unit/test_embedding_providers.py -q`

Expected: PASS。

Commit: `feat: 添加本地 BGE Embedding Provider`

### Task 3: pgvector 检索封装

**Files:**
- Create: `backend/app/rag/__init__.py`
- Create: `backend/app/rag/schemas.py`
- Create: `backend/app/rag/retriever.py`
- Test: `backend/tests/integration/test_vector_retriever.py`

**Interfaces:**
- Produces: `RetrievedChunk`，包含 `chunk_id`、`document_id`、`file_name`、来源元数据、`content`、`relevance_score`。
- Produces: `VectorRetriever.search(knowledge_base_id, query_embedding, top_k, score_threshold)`。

- [ ] **Step 1: 写数据库集成测试**

插入两个知识库和三条已知 512 维单位向量，断言：结果按余弦相关性降序、Top K 生效、阈值生效、另一个知识库的高分切片不会泄漏。

- [ ] **Step 2: 运行测试确认模块不存在**

Run: `cd backend; $env:RUN_DATABASE_TESTS='1'; uv run pytest tests/integration/test_vector_retriever.py -q`

Expected: FAIL，无法导入 `app.rag.retriever`。

- [ ] **Step 3: 封装 SQLAlchemy 查询**

```python
distance = DocumentChunk.embedding.cosine_distance(query_embedding)
score = (1 - distance).label("relevance_score")
statement = (
    select(DocumentChunk, Document.original_file_name, score)
    .join(Document, Document.id == DocumentChunk.document_id)
    .where(DocumentChunk.knowledge_base_id == knowledge_base_id)
    .where(score >= score_threshold)
    .order_by(distance)
    .limit(top_k)
)
```

把查询结果映射为不可变 `RetrievedChunk`，不向上层暴露 ORM Row。

- [ ] **Step 4: 运行测试并提交**

Run: `cd backend; $env:RUN_DATABASE_TESTS='1'; uv run pytest tests/integration/test_vector_retriever.py -q`

Expected: PASS。

Commit: `feat: 添加知识库向量检索`

### Task 4: Prompt、Chat Provider 与引用映射

**Files:**
- Modify: `backend/app/ai/contracts.py`
- Create: `backend/app/ai/chat.py`
- Create: `backend/app/rag/prompt.py`
- Create: `backend/app/rag/citations.py`
- Test: `backend/tests/unit/test_chat_provider.py`
- Test: `backend/tests/unit/test_rag_prompt.py`

**Interfaces:**
- Produces: `ChatProvider.generate(system_prompt: str, user_prompt: str) -> str`。
- Produces: `build_rag_prompt(question, chunks) -> tuple[str, str]`。
- Produces: `map_citations(answer, chunks) -> list[Citation]`。

- [ ] **Step 1: 写失败的 Prompt、HTTP 和引用测试**

断言 Prompt 给片段编号并包含来源；DeepSeek Provider POST 到 `/chat/completions` 且不在错误中暴露响应；答案 `[2] [99]` 只映射真实存在的 2。

- [ ] **Step 2: 运行测试确认缺少实现**

Run: `cd backend; uv run pytest tests/unit/test_chat_provider.py tests/unit/test_rag_prompt.py -q`

Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现 Chat 与 Prompt**

`OpenAICompatibleChatProvider` 使用 httpx AsyncClient、Bearer Key、`stream=False`；读取 `choices[0].message.content`，捕获 HTTP/JSON 错误并抛出 `CHAT_PROVIDER_ERROR`。Fake Provider 返回构造时传入的固定答案。

引用映射用正则 `r"\[(\d+)\]"` 提取编号，去重并保持首次出现顺序，只映射 `1..len(chunks)`。

- [ ] **Step 4: 运行测试并提交**

Run: `cd backend; uv run pytest tests/unit/test_chat_provider.py tests/unit/test_rag_prompt.py -q`

Expected: PASS。

Commit: `feat: 添加 RAG Prompt 与 Chat Provider`

### Task 5: RAG 服务与问答 API

**Files:**
- Create: `backend/app/rag/service.py`
- Create: `backend/app/api/v1/questions.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/unit/test_rag_service.py`
- Test: `backend/tests/integration/test_question_api.py`

**Interfaces:**
- Consumes: `EmbeddingProvider`、`VectorRetriever`、`ChatProvider`。
- Produces: `RagService.answer(knowledge_base_id, question, top_k) -> QuestionAnswer`。
- Produces: `POST /api/v1/knowledge-bases/{knowledge_base_id}/questions`。

- [ ] **Step 1: 写失败的服务测试**

覆盖正常生成、知识库不存在、无结果固定拒答且 Chat 调用次数为零、非法引用不进入响应。

- [ ] **Step 2: 实现 RagService**

固定顺序为：查询知识库、问题向量化、检索、无结果拒答、构造 Prompt、生成、映射引用。拒答文本固定为“未找到足够依据，无法根据当前知识库回答该问题。”。

- [ ] **Step 3: 写失败的 API 测试并实现路由**

请求模型：`question` 长度 1 到配置上限，`top_k` 可选且最大值由服务端配置二次校验。响应包含 answer、citations、retrieved_chunk_count、request_id。Provider 构造放在清晰的依赖函数中，测试可 override。

Run: `cd backend; uv run pytest tests/unit/test_rag_service.py tests/integration/test_question_api.py -q`

Expected: PASS。

- [ ] **Step 4: 提交**

Commit: `feat: 完成知识库问答 API`

### Task 6: 历史文档重处理

**Files:**
- Modify: `backend/app/api/v1/documents.py`
- Modify: `backend/app/knowledge/background.py`
- Test: `backend/tests/integration/test_document_reprocess_api.py`

**Interfaces:**
- Produces: `POST /api/v1/documents/{document_id}/reprocess`，返回现有文档响应结构和新的 job_id。

- [ ] **Step 1: 写失败的重处理测试**

覆盖文档不存在 404、运行中任务返回 409、已完成文档创建新任务并安排后台入库、错误状态被清空。

- [ ] **Step 2: 实现最小重处理端点**

事务内锁定文档，查询最新任务；状态为 pending/running 时返回 `DOCUMENT_PROCESSING`；否则把文档设为 pending、清空错误、创建新 IngestionJob、提交后加入现有 `run_ingestion` 后台任务。

- [ ] **Step 3: 运行测试并提交**

Run: `cd backend; $env:RUN_DATABASE_TESTS='1'; uv run pytest tests/integration/test_document_reprocess_api.py -q`

Expected: PASS。

Commit: `feat: 添加历史文档重处理接口`

### Task 7: 本地配置、文档与完整验证

**Files:**
- Modify: `README.md`
- Modify: `docs/学习笔记.md`
- Modify locally only: `backend/.env`
- Test: entire `backend/tests`

**Interfaces:**
- Produces: 可直接运行的本地 BGE + DeepSeek 配置和中文学习记录。

- [ ] **Step 1: 更新中文文档**

README 记录 1D API、BGE 首次下载和重处理命令；学习笔记解释向量检索、余弦分数、固定 RAG、引用安全以及 C# Repository/Application Service 对照。

- [ ] **Step 2: 安全写入本地配置**

只在 `backend/.env` 设置 local Embedding、DeepSeek Base URL、模型和用户提供的 Key。运行 `git check-ignore backend/.env` 并用 `git grep` 检查 Key 不在跟踪内容中；任何输出不得回显完整 Key。

- [ ] **Step 3: 执行完整自动化验证**

Run:

```powershell
cd backend
$env:DATABASE_URL='postgresql+psycopg://knowledge:knowledge@localhost:5432/knowledge'
$env:RUN_DATABASE_TESTS='1'
uv run alembic upgrade head
uv run pytest -q
uv run ruff check app tests migrations
uv run ruff format --check app tests migrations
```

Expected: 所有测试、lint 和格式检查通过。

- [ ] **Step 4: 执行真实冒烟验证**

使用小段中文样例直接调用 LocalEmbeddingProvider，断言输出 512 维且语义相近文本的余弦分数高于无关文本；再使用 DeepSeek Provider 提交一个最小上下文问答，确认返回非空中文答案。不得打印 Key。

- [ ] **Step 5: 检查密钥与提交**

Run: `git status --short; git diff --check; git grep -n "CHAT_API_KEY=.*sk-" -- . ':!backend/.env'`

Expected: `.env` 不出现在状态中，密钥扫描无结果。

Commit: `docs: 完成阶段 1D 学习笔记`


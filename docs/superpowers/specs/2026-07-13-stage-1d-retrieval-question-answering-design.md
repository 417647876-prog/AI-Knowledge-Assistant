# 阶段 1D：本地语义检索与问答设计

## 1. 目标

阶段 1D 在阶段 1C 已生成的文档切片之上完成固定两阶段 RAG 闭环：先在指定知识库中检索可靠片段，再让 DeepSeek 严格依据片段生成带引用的中文答案。

本阶段同时把测试用 Fake Embedding 升级为本地真实 Embedding：使用 `BAAI/bge-small-zh-v1.5` 生成 512 维向量，并继续保留 Fake Provider 供自动化测试使用。

本阶段不实现 BM25、混合检索、Rerank、Agent 自主工具调用、流式响应和前端界面。

## 2. 核心决策

1. 使用固定的 `Retrieve → Generate` 流程，每次问答都先检索，不让模型决定是否检索。
2. 使用 PostgreSQL + pgvector 做知识库范围内的精确余弦检索。
3. 使用本地 `BAAI/bge-small-zh-v1.5` 生成真实语义向量。
4. 使用 OpenAI Chat Completions 兼容协议接入 DeepSeek，默认模型为 `deepseek-v4-flash`。
5. 保留 Fake Embedding 和 Fake Chat Provider，使测试不下载模型、不访问外网、不产生费用。
6. API Key 只保存在被 Git 忽略的本地 `.env`，不得出现在源码、文档、测试、日志或 Git 提交中。

## 3. 总体数据流

### 3.1 文档入库

```text
上传文档
  → 解析、清洗与切片
  → LocalEmbeddingProvider 生成 512 维归一化向量
  → PostgreSQL + pgvector 保存
```

### 3.2 用户问答

```text
用户问题
  → 同一个 LocalEmbeddingProvider 生成问题向量
  → VectorRetriever 在指定知识库内做余弦检索
  → Top K 与相关性阈值过滤
  → PromptBuilder 生成带编号上下文
  → DeepSeekChatProvider 生成答案
  → 后端把模型编号映射为数据库中的真实引用
```

文档和问题必须使用同一个 Embedding 模型、相同维度和相同归一化规则。不同模型生成的向量不可混用。

## 4. 组件边界

### 4.1 EmbeddingProvider

扩展现有契约，使它同时支持文档批量向量化和问题单条向量化：

```python
class EmbeddingProvider(Protocol):
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...
```

实现包括：

- `FakeEmbeddingProvider`：生成确定性的 512 维测试向量。
- `LocalEmbeddingProvider`：封装 `sentence-transformers` 和 BGE Small。

本地模型采用延迟加载，同一进程只创建一个模型实例。设备配置支持 `auto`、`cuda` 和 `cpu`；`auto` 在 CUDA 可用时使用 GPU，否则回退 CPU。文档采用批量编码，输出向量必须归一化。

### 4.2 VectorRetriever

向量 SQL 只存在于检索组件中，不散落在 API 或 RAG 服务里：

```python
async def search(
    knowledge_base_id: UUID,
    query_embedding: list[float],
    top_k: int,
    score_threshold: float,
) -> list[RetrievedChunk]: ...
```

检索规则：

- 必须使用 `knowledge_base_id` 过滤。
- 使用 pgvector 余弦距离排序。
- 将距离转换为越大越相关的 `relevance_score = 1 - cosine_distance`。
- 先应用相关性阈值，再返回最多 `top_k` 条结果。
- 初期使用精确检索，不增加 HNSW 索引。

`RetrievedChunk` 是只读 DTO，包含切片、文档来源与相关性分数。`RagService` 不接触 SQLAlchemy 查询细节。

### 4.3 ChatProvider

```python
class ChatProvider(Protocol):
    async def generate(self, system_prompt: str, user_prompt: str) -> str: ...
```

实现包括：

- `FakeChatProvider`：为单元测试和集成测试返回可预测结果。
- `OpenAICompatibleChatProvider`：通过 `/chat/completions` 调用兼容服务。

本地运行配置为 DeepSeek：

```text
CHAT_PROVIDER=deepseek
CHAT_BASE_URL=https://api.deepseek.com
CHAT_MODEL=deepseek-v4-flash
CHAT_API_KEY=本地密钥（只写入被 Git 忽略的 .env）
```

### 4.4 PromptBuilder

PromptBuilder 负责把检索片段转换为稳定、可测试的模型输入。系统约束要求模型：

- 只能依据给定上下文回答。
- 上下文没有答案时明确拒答。
- 不得编造政策、数字、日期、文件名或页码。
- 只能使用后端提供的 `[1]`、`[2]` 等引用编号。
- 默认使用中文回答。

上下文中的文件名、页码、章节和正文均来自数据库。

### 4.5 RagService

RagService 只编排应用流程：

1. 校验知识库存在。
2. 清理问题两端空白。
3. 调用 EmbeddingProvider 生成问题向量。
4. 调用 VectorRetriever 检索。
5. 无结果时直接拒答，不调用 ChatProvider。
6. 构建 Prompt 并生成答案。
7. 从答案中提取合法引用编号。
8. 只返回能够映射到真实检索片段的引用。

检索方法未来可以复用为 Agent Tool，但 1D 不让模型自主决定是否调用检索。

## 5. API 设计

### 5.1 请求

```http
POST /api/v1/knowledge-bases/{knowledge_base_id}/questions
```

```json
{
  "question": "员工入职满一年有多少天年假？",
  "top_k": 5
}
```

约束：

- `question` 去除两端空白后不能为空。
- 问题长度设置明确上限，避免异常大请求。
- `top_k` 默认 5，并限制在安全范围内。
- 相关性阈值使用服务端配置，客户端不能任意降低。

### 5.2 响应

```json
{
  "answer": "员工入职满一年可享受 5 天带薪年假。[1]",
  "citations": [
    {
      "citation_id": 1,
      "document_id": "文档 UUID",
      "file_name": "员工手册.pdf",
      "page_number": 12,
      "section_title": null,
      "content": "员工入职满一年后可享受 5 天带薪年假。",
      "relevance_score": 0.91
    }
  ],
  "retrieved_chunk_count": 1,
  "request_id": "请求追踪 ID"
}
```

`retrieved_chunk_count` 表示通过阈值的片段数量。`citations` 只包含答案实际引用且能映射成功的片段。

## 6. 拒答与错误处理

- 知识库不存在：返回统一的 `404` 业务错误。
- 问题或 `top_k` 无效：返回 FastAPI/Pydantic 的结构化校验错误。
- 没有片段通过阈值：返回固定的“未找到足够依据”答案、空引用和零检索数，不调用 Chat Provider。
- 本地模型加载或编码失败：记录技术日志，对外返回安全的 Embedding 服务错误。
- DeepSeek 超时、限流或响应无效：记录安全日志，对外返回统一的 Chat Provider 错误。
- 模型输出不存在的引用编号：忽略该编号，不伪造来源。
- 日志和错误响应不得包含 API Key、Authorization 请求头或完整第三方响应体。

## 7. 数据库迁移与历史数据

`BAAI/bge-small-zh-v1.5` 输出 512 维向量，因此：

- `document_chunks.embedding` 从 `vector(1536)` 迁移为 `vector(512)`。
- `EMBEDDING_DIMENSIONS` 默认值改为 512。
- 迁移清除旧 `document_chunks`，因为旧 Fake 向量不能转换成 BGE 向量。
- 保留知识库、文档、历史任务记录和原始上传文件，并把受影响文档状态重置为 `pending`。
- 增加 `POST /api/v1/documents/{document_id}/reprocess`，为已有文档创建新的入库任务并复用现有后台入库流程。
- 重处理接口只允许当前没有运行中任务的文档执行；重复请求返回冲突错误，避免同一文档并发写入。
- 现有文档必须调用重处理接口生成 BGE 向量，之后才能参与真实语义检索。

迁移必须以事务执行。维度配置、数据库列和 Provider 输出不一致时立即报错，不能静默截断或补零。

## 8. 配置

新增或调整以下配置：

```text
EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
EMBEDDING_DIMENSIONS=512
EMBEDDING_DEVICE=auto
EMBEDDING_BATCH_SIZE=32

CHAT_PROVIDER=fake|deepseek
CHAT_BASE_URL=https://api.deepseek.com
CHAT_API_KEY=
CHAT_MODEL=deepseek-v4-flash
CHAT_TIMEOUT_SECONDS=30

RAG_TOP_K_DEFAULT=5
RAG_TOP_K_MAX=20
RAG_SCORE_THRESHOLD=0.55
RAG_QUESTION_MAX_LENGTH=2000
```

`0.55` 是第一版保守默认值，本地真实冒烟测试会记录典型中文问题的分数分布；只有取得测试证据后才调整。自动化测试不依赖真实模型输出的精确浮点值。

## 9. 测试策略

### 9.1 单元测试

- 配置组合和缺失密钥校验。
- Fake Provider 的维度、确定性和文档/问题一致性。
- Local Provider 的延迟加载、设备选择、批处理和维度校验；通过替身避免测试下载模型。
- Prompt 内容、上下文编号和来源格式。
- 合法与非法引用编号映射。
- 无检索结果时不调用 Chat Provider。
- Chat Provider 的成功、超时、限流和无效响应处理。

### 9.2 数据库集成测试

- 余弦相似度排序正确。
- Top K 限制正确。
- 相关性阈值过滤正确。
- 不同知识库的切片严格隔离。
- 512 维向量能够正常写入和查询。

### 9.3 API 集成测试

- 正常问答返回答案、真实引用、检索数量和 request_id。
- 无相关内容时返回拒答且不调用 Chat Provider。
- 知识库不存在返回 404。
- 历史文档能够通过重处理接口重新生成 BGE 向量，并拒绝同一文档的并发重处理。
- 请求校验和 Provider 失败返回统一错误协议。

### 9.4 本地真实冒烟测试

1. 下载并加载 BGE Small。
2. 上传一份中文测试文档并重新入库。
3. 用语义不同但含义相近的问题验证检索。
4. 使用本地 `.env` 中的 DeepSeek Key 生成带引用答案。
5. 确认日志、响应和 Git 变更均未泄露 Key。

真实冒烟测试允许产生少量 DeepSeek 费用，但不得成为自动化测试的必需条件。

## 10. 完成标准

- 文档和问题能够使用本地 BGE Small 生成 512 维真实向量。
- pgvector 能在指定知识库内按余弦相似度返回 Top K 片段。
- 低相关性问题不会调用 DeepSeek。
- 有可靠片段时，DeepSeek 能生成中文答案。
- 所有返回引用均能映射到数据库中的真实文档切片。
- 自动化测试默认离线、无费用且稳定通过。
- DeepSeek Key 只存在于本地忽略文件中，Git 历史不包含密钥。

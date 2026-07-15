# AI 企业知识库助手：第一阶段 RAG 后端设计文档

## 1. 文档信息

- 项目名称：AI 企业知识库助手（AI Knowledge Assistant）
- 项目路径：`D:\学习\AI-Knowledge-Assistant`
- 文档日期：2026-07-10
- 当前阶段：第一阶段——RAG 后端核心闭环
- 目标岗位：AI 应用开发工程师、Python AI 应用工程师、.NET + AI 应用工程师

## 2. 项目背景

企业内部的制度、产品说明、技术资料和业务报表通常分散在 PDF、Word、Excel、Markdown 等文件中。传统关键词搜索难以理解自然语言问题，通用大模型又无法直接访问企业私有资料，也可能在缺少依据时生成看似合理但实际错误的答案。

本项目使用 RAG（Retrieval-Augmented Generation，检索增强生成）架构，将企业文档解析、切片并向量化。在用户提问时，系统先从指定知识库中检索相关片段，再让大模型严格依据片段生成答案，并返回真实来源。

项目不是简单的“大模型 API 聊天 Demo”，而是包含文档处理、任务状态、数据持久化、向量检索、模型抽象、错误处理、来源追踪和自动化测试的可扩展 AI 应用。

## 3. 第一阶段目标

第一阶段只实现一条可运行、可测试、可演示的后端垂直链路：

```text
创建知识库
  → 上传文档
  → 解析正文与来源信息
  → 清洗并切片
  → 生成 Embedding
  → 保存业务数据与向量
  → 用户提问
  → 检索相关片段
  → 调用聊天模型
  → 返回答案与来源引用
```

第一阶段完成标准：

1. 能通过 FastAPI 创建知识库。
2. 能上传 PDF、Word、Excel、Markdown、TXT 文档。
3. 能查看文档处理状态和明确的失败原因。
4. 能将文本切片和向量保存到 PostgreSQL + pgvector。
5. 能在指定知识库范围内进行相似度检索。
6. 能使用 OpenAI 兼容接口调用 Embedding 与聊天模型。
7. 能在没有充分依据时拒绝编造答案。
8. 能返回文件名、页码或工作表等结构化引用。
9. 核心业务能够使用假模型运行自动化测试，不依赖真实 API。

## 4. 第一阶段不包含的功能

为了尽快验证 RAG 核心能力，以下功能进入后续阶段：

- Vue3 前端界面
- 用户注册、登录和 JWT 身份认证
- 用户、部门和角色权限体系
- 多轮聊天历史
- OCR 扫描件识别
- 混合检索和 Rerank 重排序
- Agent 自主规划
- 消息队列和独立 Worker
- Kubernetes 或云平台部署

第一阶段的数据模型和模块边界需要为登录、权限与异步任务预留扩展空间，但不提前实现这些功能。

## 5. 技术选型

| 领域 | 第一阶段技术 | 选择理由 |
|---|---|---|
| 编程语言 | Python 3.12 | AI 生态成熟，类型标注与现代异步支持完善 |
| Web API | FastAPI | 支持异步、依赖注入、Pydantic 和自动 OpenAPI 文档 |
| 数据校验 | Pydantic 2 | 统一配置、请求和响应模型校验 |
| ORM | SQLAlchemy 2 | 显式数据模型、事务和异步数据库访问 |
| 数据迁移 | Alembic | 数据库结构可追踪、可升级、可回滚 |
| 关系数据库 | PostgreSQL 16 | 保存知识库、文档、任务和切片业务数据 |
| 向量扩展 | pgvector | 在 PostgreSQL 中保存和检索 Embedding 向量 |
| PostgreSQL 驱动 | psycopg 3 | 与当前 SQLAlchemy、pgvector 生态兼容 |
| AI 组件 | LangChain 独立组件包 | 用于文本切片、模型适配和 Prompt 组件 |
| PDF 解析 | PyMuPDF | 提取文本并保留页码 |
| Word 解析 | python-docx | 提取段落与表格 |
| Excel 解析 | openpyxl | 保留工作表和行号来源信息 |
| 测试 | pytest | 单元测试和集成测试 |
| 容器 | Docker Compose | 本地启动 PostgreSQL + pgvector 和后端服务 |

### 5.1 LangChain 的使用边界

LangChain 只用于适合标准化的 AI 组件：

- `RecursiveCharacterTextSplitter` 文本切片
- OpenAI 兼容的 Embedding 与 Chat 模型适配
- Prompt 模板组件

LangChain 不负责以下核心业务：

- 知识库与文档业务表
- 文件生命周期
- 文档处理状态
- 数据隔离条件
- 数据库事务
- 来源引用映射
- API 错误模型

这样既能体现 LangChain 使用经验，又能避免核心业务被框架内部实现隐藏。

### 5.2 模型配置原则

Embedding 模型和 Chat 模型分别配置，允许来自不同供应商：

```text
EMBEDDING_BASE_URL
EMBEDDING_API_KEY
EMBEDDING_MODEL
EMBEDDING_DIMENSIONS

CHAT_BASE_URL
CHAT_API_KEY
CHAT_MODEL
```

切换 Chat 模型不要求重新处理文档。切换 Embedding 模型或向量维度时，必须重新生成已有文档向量。应用启动时需要检查配置维度与数据库向量字段是否一致。

第一阶段数据库迁移将向量维度固定为 `1536`，默认真实接口示例使用 `text-embedding-3-small`。Fake Embedding Provider 也必须输出 1536 维向量。若实际供应商使用其他维度，需要先修改迁移配置并重建向量数据，不能在同一个向量字段中混用不同维度。

## 6. 架构方案

项目采用“轻量分层 + 按业务能力组织”的模块化单体。第一阶段不拆微服务，避免增加部署、事务和调试成本。

```text
FastAPI API
    │
    ▼
Knowledge Application Service
    ├── Document Ingestion Service
    └── RAG Question Answering Service
          │
          ├── Parser / Splitter
          ├── EmbeddingProvider 接口
          ├── ChatProvider 接口
          ├── Retriever 接口
          └── Repository 接口
                    │
                    ▼
          PostgreSQL + pgvector
```

与 C# 企业应用的概念对应关系：

| Python 项目 | C# 常见概念 |
|---|---|
| FastAPI Router | ASP.NET Core Controller |
| Pydantic Model | Request/Response DTO |
| Application Service | 应用服务 |
| Protocol/抽象基类 | Interface |
| SQLAlchemy Session | EF Core DbContext |
| Alembic Migration | EF Core Migration |
| FastAPI Depends | 依赖注入 |

## 7. 项目目录结构

```text
AI-Knowledge-Assistant/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── dependencies.py
│   │   │   ├── error_handlers.py
│   │   │   └── v1/
│   │   │       ├── health.py
│   │   │       ├── knowledge_bases.py
│   │   │       ├── documents.py
│   │   │       └── questions.py
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── exceptions.py
│   │   │   └── logging.py
│   │   ├── db/
│   │   │   ├── base.py
│   │   │   ├── session.py
│   │   │   └── models/
│   │   ├── knowledge/
│   │   │   ├── schemas.py
│   │   │   ├── repositories.py
│   │   │   ├── ingestion_service.py
│   │   │   ├── rag_service.py
│   │   │   ├── retriever.py
│   │   │   ├── chunking.py
│   │   │   └── parsers/
│   │   │       ├── base.py
│   │   │       ├── pdf.py
│   │   │       ├── word.py
│   │   │       ├── excel.py
│   │   │       └── text.py
│   │   ├── ai/
│   │   │   ├── contracts.py
│   │   │   ├── openai_compatible.py
│   │   │   └── prompts.py
│   │   └── main.py
│   ├── migrations/
│   ├── tests/
│   │   ├── unit/
│   │   ├── integration/
│   │   └── fixtures/
│   ├── pyproject.toml
│   └── .env.example
├── deploy/
│   └── docker-compose.yml
├── docs/
│   └── superpowers/
│       ├── specs/
│       └── plans/
└── README.md
```

目录按业务责任拆分，避免出现一个包含上传、解析、向量化、检索和模型调用的超大 Service 文件。

## 8. 核心数据模型

所有主键采用 UUID。所有时间使用 UTC 存储，在前端展示时再转换时区。

### 8.1 knowledge_bases

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 主键 |
| name | VARCHAR(100) | 知识库名称 |
| description | TEXT | 说明，可为空 |
| owner_id | UUID | 后续关联用户，第一阶段可为空 |
| created_at | TIMESTAMPTZ | 创建时间 |
| updated_at | TIMESTAMPTZ | 更新时间 |

### 8.2 documents

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 主键 |
| knowledge_base_id | UUID | 所属知识库 |
| original_file_name | VARCHAR(255) | 用户上传的文件名 |
| stored_file_name | VARCHAR(255) | 服务端安全文件名 |
| content_type | VARCHAR(100) | MIME 类型 |
| file_extension | VARCHAR(20) | 规范化扩展名 |
| file_size | BIGINT | 字节数 |
| file_hash | VARCHAR(64) | SHA-256，用于重复检测 |
| status | VARCHAR(30) | pending、parsing、embedding、ready、failed |
| error_code | VARCHAR(50) | 结构化错误码，可为空 |
| error_message | TEXT | 对用户安全的失败说明，可为空 |
| created_at | TIMESTAMPTZ | 创建时间 |
| updated_at | TIMESTAMPTZ | 更新时间 |

同一知识库中对 `knowledge_base_id + file_hash` 建立唯一约束，避免完全相同的文件被重复处理。

### 8.3 document_chunks

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 主键 |
| document_id | UUID | 所属文档 |
| knowledge_base_id | UUID | 冗余隔离字段，便于强制检索过滤 |
| chunk_index | INTEGER | 在文档中的顺序 |
| content | TEXT | 切片正文 |
| content_hash | VARCHAR(64) | 切片内容哈希 |
| page_number | INTEGER | PDF 页码，可为空 |
| sheet_name | VARCHAR(100) | Excel 工作表，可为空 |
| row_start | INTEGER | Excel 起始行，可为空 |
| section_title | VARCHAR(500) | 章节标题，可为空 |
| start_index | INTEGER | 原文字符起点，可为空 |
| metadata | JSONB | 其他可扩展来源信息 |
| embedding | VECTOR(1536) | 第一阶段固定为 1536 维 |
| created_at | TIMESTAMPTZ | 创建时间 |

向量查询必须同时包含 `knowledge_base_id` 条件，不能先跨知识库查询再由应用层过滤。

### 8.4 ingestion_jobs

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 主键 |
| document_id | UUID | 所属文档 |
| status | VARCHAR(30) | pending、running、succeeded、failed |
| stage | VARCHAR(30) | parse、clean、split、embed、store |
| chunk_count | INTEGER | 产生的切片数 |
| started_at | TIMESTAMPTZ | 开始时间，可为空 |
| finished_at | TIMESTAMPTZ | 完成时间，可为空 |
| error_code | VARCHAR(50) | 失败错误码，可为空 |
| error_message | TEXT | 失败说明，可为空 |

第一阶段使用 FastAPI 进程内后台任务执行文档处理，并记录任务状态。该方式只用于本地开发和面试演示，不承诺进程重启后的任务恢复。后续迁移到持久化队列 Worker 时，API 契约和业务表无需推翻重做。

## 9. 文档处理设计

### 9.1 文件安全校验

上传时执行：

1. 文件扩展名必须在白名单中。
2. MIME 类型需要与允许范围匹配。
3. 第一阶段单文件大小上限为 20 MB。
4. 文件名只用于展示，不直接作为服务器保存路径。
5. 服务端保存名使用 UUID，防止路径穿越和文件覆盖。
6. 计算 SHA-256，并检查同知识库是否已存在相同文件。

### 9.2 解析器统一输出

不同格式解析器统一返回 `ParsedSection`：

```text
text            正文
page_number     PDF 页码
sheet_name      Excel 工作表
row_start       Excel 起始行
section_title   标题
metadata        其他来源信息
```

解析器不负责数据库写入和模型调用，便于单独测试和替换。

### 9.3 各格式处理规则

- PDF：使用 PyMuPDF 按页提取文本，保留一页一来源；没有可提取文字时标记为不支持的扫描型 PDF。
- Word：按原始顺序提取段落和表格，尽可能保留标题层级。
- Excel：按工作表和数据行转换成带表头的可读文本，保留工作表名和行号。
- Markdown：保留标题层级，优先按标题分段。
- TXT：尝试 UTF-8 与 UTF-8 BOM；无法解码时返回明确错误，不静默替换成乱码。

### 9.4 文本切片

第一阶段采用递归字符切片：

- `chunk_size` 默认 800 个字符。
- `chunk_overlap` 默认 120 个字符。
- 分隔符包含段落、换行、空格以及中文句号、问号、叹号、分号、逗号和顿号。
- 空白切片不入库。
- 每个切片继承文档和解析段落的来源元数据。
- 切片参数放入配置，后续通过评估数据调整。

## 10. 文档入库数据流

```text
接收 UploadFile
  → 校验文件
  → 计算 SHA-256
  → 保存原始文件
  → 创建 Document(status=pending)
  → 创建 IngestionJob(status=pending)
  → status=parsing
  → 按格式解析 ParsedSection
  → 文本清洗
  → 递归切片
  → status=embedding
  → 批量调用 EmbeddingProvider
  → 在同一数据库事务中写入全部 DocumentChunk
  → Document(status=ready)
  → IngestionJob(status=succeeded)
```

失败处理原则：

- 解析或向量化失败时，不保留半套切片。
- 原始文件可以保留，便于排查和重新处理。
- 文档和任务更新为 `failed`，写入结构化错误码。
- 日志记录技术细节，API 只返回安全、可理解的错误信息。

## 11. RAG 问答设计

### 11.1 第一阶段架构

采用固定的两阶段 RAG：

```text
Retrieve 检索 → Generate 生成
```

不让 Agent 自主决定是否检索，保证流程可控、延迟可预测并易于测试。

### 11.2 检索步骤

1. 校验知识库存在。
2. 对问题做基础清洗，不改变原始语义。
3. 使用与文档相同的 Embedding 模型生成问题向量。
4. 在 `knowledge_base_id` 范围内计算余弦距离。
5. 默认取 Top 5 候选切片。
6. 转换成统一的 `relevance_score`，数值越大表示越相关。
7. 应用可配置的最低相关性阈值。
8. 没有满足阈值的切片时直接返回“未找到足够依据”，不调用 Chat 模型。

第一阶段数据量较小时使用精确向量检索。达到需要优化的数据规模并取得基准数据后，再增加 HNSW 索引。

### 11.3 Prompt 约束

系统 Prompt 必须要求：

- 仅根据给定上下文回答。
- 上下文没有答案时明确拒答。
- 不得编造政策、数字、日期、文件名或页码。
- 使用后端提供的引用编号，例如 `[1]`。
- 回答使用中文，除非用户明确要求其他语言。

上下文格式：

```text
[1] 文件：员工手册.pdf；页码：12
员工入职满一年后可享受 5 天带薪年假。

[2] 文件：休假制度.docx；章节：申请流程
年假应提前三个工作日提交申请。
```

### 11.4 结构化引用

模型只引用编号，真实引用由后端映射：

```json
{
  "answer": "员工入职满一年后可享受 5 天带薪年假。[1]",
  "citations": [
    {
      "citation_id": 1,
      "document_id": "文档 UUID",
      "file_name": "员工手册.pdf",
      "page_number": 12,
      "content": "员工入职满一年后可享受 5 天带薪年假。",
      "relevance_score": 0.91
    }
  ]
}
```

文件名、页码和片段来自数据库，不允许由模型自由生成。

## 12. API 设计

统一前缀：`/api/v1`

### 12.1 健康检查

```text
GET /health
```

返回应用状态；另提供 readiness 检查确认数据库和 pgvector 可用。

### 12.2 创建知识库

```text
POST /api/v1/knowledge-bases
```

请求：

```json
{
  "name": "人事制度",
  "description": "员工手册和考勤制度"
}
```

### 12.3 获取知识库列表

```text
GET /api/v1/knowledge-bases
```

### 12.4 上传文档

```text
POST /api/v1/knowledge-bases/{knowledge_base_id}/documents
Content-Type: multipart/form-data
```

文件完成校验和落盘后返回 `202 Accepted`，响应包含 `document_id`、`job_id` 和当前状态；文档解析在 FastAPI 进程内后台任务中继续执行。

### 12.5 查询文档状态

```text
GET /api/v1/documents/{document_id}
```

返回文档状态、切片数量和安全的失败说明。

### 12.6 知识库问答

```text
POST /api/v1/knowledge-bases/{knowledge_base_id}/questions
```

请求：

```json
{
  "question": "员工入职满一年后有多少天年假？",
  "top_k": 5
}
```

响应包含：

- `answer`
- `citations`
- `retrieved_chunk_count`
- `request_id`

## 13. 统一错误处理

错误响应格式：

```json
{
  "error": {
    "code": "UNSUPPORTED_FILE_TYPE",
    "message": "当前不支持该文件格式。",
    "request_id": "请求追踪 ID"
  }
}
```

主要错误：

| 场景 | HTTP 状态码 | 错误码 |
|---|---:|---|
| 知识库不存在 | 404 | KNOWLEDGE_BASE_NOT_FOUND |
| 文档不存在 | 404 | DOCUMENT_NOT_FOUND |
| 不支持的文件类型 | 415 | UNSUPPORTED_FILE_TYPE |
| 文件过大 | 413 | FILE_TOO_LARGE |
| 文档为空或无法解析 | 422 | DOCUMENT_CONTENT_EMPTY |
| 扫描型 PDF | 422 | OCR_REQUIRED |
| 相同文件重复上传 | 409 | DUPLICATE_DOCUMENT |
| Embedding 服务失败 | 502 | EMBEDDING_PROVIDER_ERROR |
| Chat 服务失败 | 502 | CHAT_PROVIDER_ERROR |
| 数据库暂不可用 | 503 | DATABASE_UNAVAILABLE |

## 14. 测试策略

开发遵循测试先行：先编写失败测试并确认失败原因，再实现最少代码使其通过。

### 14.1 单元测试

- 配置校验：缺少必须配置时启动失败。
- 文件校验：扩展名、大小、空文件和重复文件。
- PDF 解析：正文与页码正确对应。
- Word 解析：段落和表格顺序正确。
- Excel 解析：表头、工作表和行号正确保留。
- 中文切片：重叠与中文标点边界正确。
- Retriever：始终附带知识库过滤条件。
- RAG Service：无相关片段时不调用 ChatProvider。
- 引用映射：模型编号只能映射到真实检索片段。

### 14.2 测试替身

提供确定性的：

- `FakeEmbeddingProvider`
- `FakeChatProvider`
- 必要时的内存 Repository

Fake Embedding 对固定输入返回固定向量，使测试可重复且不消耗 API 额度。

### 14.3 集成测试

- Alembic 能在空 PostgreSQL 数据库完成升级。
- pgvector 扩展可用。
- 切片和向量能在一个事务中写入。
- 向量检索排序符合预期。
- 处理失败时切片事务回滚。
- FastAPI 上传和问答接口返回正确状态码与响应结构。

### 14.4 冒烟测试

在 Docker Compose 环境中完成：

```text
启动服务
  → 创建知识库
  → 上传测试文档
  → 等待 ready
  → 提问
  → 验证答案包含引用
```

默认冒烟测试使用 Fake Provider；真实模型测试由显式环境变量开启，防止 CI 意外消耗额度。

## 15. 日志与可观测性

使用结构化日志，至少包含：

- `request_id`
- `knowledge_base_id`
- `document_id`
- `job_id`
- 当前处理阶段
- 耗时
- 切片数量
- 模型名称
- 错误类型

禁止记录：

- API Key
- 数据库密码
- 完整敏感文档正文
- 用户密码或 Token

第一阶段记录关键耗时，为后续增加指标系统提供依据：

- 文档解析耗时
- Embedding 耗时
- 向量检索耗时
- Chat 模型耗时
- 单次问答总耗时

## 16. 安全与数据隔离

第一阶段尚未实现登录，但以下规则从一开始执行：

1. 所有检索都必须使用 `knowledge_base_id` 过滤。
2. 不使用原始文件名拼接磁盘路径。
3. 不信任扩展名，配合 MIME 类型和实际解析结果校验。
4. API Key 只从环境变量读取，不写入仓库。
5. `.env.example` 只保留配置名称和示例占位值。
6. API 错误不暴露堆栈、SQL、服务器路径或密钥。
7. 删除文档时必须同时删除原始文件、切片和向量，操作保持一致性。

后续加入用户系统后，`knowledge_base_id` 过滤前还需要执行用户与知识库的授权校验。

## 17. 开发阶段规划

### 阶段 1A：项目基础与数据库

- Python 项目与依赖管理
- FastAPI 应用工厂
- 配置和统一错误模型
- PostgreSQL + pgvector Docker 服务
- SQLAlchemy 模型与 Alembic
- 健康检查

### 阶段 1B：知识库与文档解析

- 知识库 API
- 文件校验和安全存储
- PDF、Word、Excel、Markdown、TXT 解析器
- 文档与任务状态

### 阶段 1C：切片与向量入库

- 中文递归切片
- EmbeddingProvider 抽象
- Fake 与 OpenAI 兼容实现
- 向量事务写入

### 阶段 1D：检索与问答

- pgvector 余弦检索
- 相关性阈值
- Prompt 构建
- ChatProvider 抽象
- 结构化引用
- 问答 API

### 阶段 1E：验证与展示材料

- 单元与集成测试整理
- Docker 冒烟测试
- 中文 README
- API 调用示例
- 架构图和面试演示脚本

## 18. 后续阶段路线

### 第二阶段：产品化

- Vue3 + Element Plus
- 用户注册、登录、JWT
- 用户与知识库权限隔离
- 文档列表、状态展示和删除
- 流式回答
- 聊天历史

### 第三阶段：检索质量

- PostgreSQL 全文检索与向量混合检索
- Rerank 重排序
- Query Rewrite
- 多轮问题改写
- RAG 评估数据集和 Recall@K 等指标

### 第四阶段：企业能力

- Celery 或其他任务队列
- 独立 Worker
- 对象存储
- 限流、审计日志和监控
- 多租户隔离
- CI/CD 和在线演示环境

## 19. 关键设计决策

1. 先做固定两阶段 RAG，不在核心链路中使用 Agent。
2. 使用 PostgreSQL 同时管理业务数据，使用 pgvector 管理向量。
3. 核心业务表由项目自行设计，不依赖 LangChain 自动生成的表结构。
4. Chat 和 Embedding 模型分别抽象、分别配置。
5. 测试默认使用 Fake Provider，真实模型调用是显式测试。
6. 来源引用由数据库元数据映射，模型不负责生成真实文件名和页码。
7. 第一阶段使用进程内后台任务处理文档，API 和状态表按可迁移到持久化队列的方式设计。
8. 初期使用精确向量检索，有实际性能证据后再增加 HNSW。

## 20. 验收演示

面试演示控制在五分钟：

1. 打开 FastAPI Swagger。
2. 创建“人事制度”知识库。
3. 上传包含年假制度的 PDF。
4. 查看文档从处理中变为 `ready`。
5. 提问“员工入职满一年后有多少天年假？”
6. 展示答案、文件名、页码和相关片段。
7. 提问一个资料中不存在的问题，展示系统拒绝编造。
8. 简要展示模块边界、数据表和自动化测试结果。

该演示同时证明文档工程、向量检索、大模型集成、来源追踪、错误处理和工程测试能力。

# 阶段 3B：PostgreSQL 混合检索实施计划

> **执行要求：** 使用 `executing-plans` 逐 Task 执行；除非用户明确要求，不派发子代理。所有步骤用本文复选框跟踪。

**目标：** 在保留纯向量回退路径的基础上，增加中文关键词检索和 RRF 排名融合。

**架构：** 入库时生成稳定的中文检索 Token；PostgreSQL 用生成的 `tsvector` 和 GIN 索引完成关键词检索。`HybridRetriever` 并行组织向量与关键词候选，再用纯函数 `rrf_fuse` 融合。

**技术栈：** Python 3.12、SQLAlchemy 2、Alembic、PostgreSQL `tsvector`、pgvector、pytest。

## 全局约束

- 开始前 `docs/阶段3执行进度.md` 中 3A 必须为 `已完成`。
- 不新增 Elasticsearch、OpenSearch、中文 PostgreSQL 扩展或外部搜索服务。
- 中文分词使用仓库内确定性实现，避免增加运行依赖。
- 所有检索强制过滤 `knowledge_base_id`。
- `RAG_RETRIEVAL_MODE=vector` 必须保持原行为。
- 历史文档通过现有重处理接口补齐检索字段，不在迁移中调用 Embedding 模型。

---

## 文件职责图

| 文件 | 职责 |
|---|---|
| `backend/app/knowledge/search_tokens.py` | 中文、英文、数字的确定性检索 Token |
| `backend/app/db/models/document_chunk.py` | `search_text` 与生成的 `search_vector` |
| `backend/migrations/versions/20260715_05_hybrid_search.py` | 字段、生成列和 GIN 索引迁移 |
| `backend/app/rag/contracts.py` | 统一 Retriever 协议 |
| `backend/app/rag/keyword_retriever.py` | PostgreSQL 关键词检索 |
| `backend/app/rag/fusion.py` | RRF 纯函数 |
| `backend/app/rag/hybrid_retriever.py` | 两路召回与融合 |
| `backend/app/rag/retriever.py` | 现有向量检索适配统一协议 |

## Task 1：确定性中文检索 Token

**推荐模型：Terra**

**只读取：** 本 Task、`backend/app/knowledge/cleaning.py`、`backend/tests/unit/test_text_cleaning.py`。

**文件：**

- 新建：`backend/app/knowledge/search_tokens.py`
- 新建：`backend/tests/unit/test_search_tokens.py`

**接口：**

```python
def build_search_text(text: str) -> str: ...
```

- [x] **Step 1：写中文双字、ASCII 单词、数字和重复 Token 测试**

```python
def test_build_search_text_handles_chinese_codes_and_words() -> None:
    result = build_search_text("VPN 账号连续输错 5 次将锁定")
    assert result.split() == [
        "vpn", "账号", "号连", "连续", "续输", "输错",
        "5", "次将", "将锁", "锁定",
    ]
```

- [x] **Step 2：实现确定性扫描**

规则：连续 ASCII 字母数字归一为小写单词；连续中文生成相邻重叠双字，只有一个汉字时保留单字；标点和空白仅作分隔；按首次出现顺序去重；空文本返回空字符串。

- [x] **Step 3：验证并提交**

运行：`uv run pytest tests/unit/test_search_tokens.py -q`

提交：`git commit -m "feat: 增加中文检索Token生成"`

## Task 2：检索字段、迁移与入库写入

**推荐模型：Sol**

**只读取：** `backend/app/db/models/document_chunk.py`、`backend/app/knowledge/ingestion_service.py`、最新 Alembic 迁移、对应测试。

**文件：**

- 修改：`backend/app/db/models/document_chunk.py`
- 修改：`backend/app/knowledge/ingestion_service.py`
- 新建：`backend/migrations/versions/20260715_05_hybrid_search.py`
- 修改：`backend/tests/unit/test_database_metadata.py`
- 修改：`backend/tests/integration/test_database_schema.py`
- 修改：`backend/tests/integration/test_vector_ingestion.py`

**模型字段：**

```python
search_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
search_vector: Mapped[Any] = mapped_column(
    TSVECTOR,
    Computed("to_tsvector('simple', search_text)", persisted=True),
)
```

- [x] **Step 1：先写元数据、迁移后字段和入库 Token 测试**
- [x] **Step 2：迁移增加字段和索引**

迁移操作顺序固定为：增加非空 `search_text`（临时 server default 空串）→ 增加持久化生成列 `search_vector` → 创建 `ix_document_chunks_search_vector` GIN 索引 → 保留空串 server default 以兼容直接构造的旧测试数据。

- [x] **Step 3：入库时写 `search_text=build_search_text(chunk.content)`**
- [x] **Step 4：验证升级、降级和重处理**

运行：`uv run alembic upgrade head`

运行：`$env:RUN_DATABASE_TESTS='1'; uv run pytest tests/integration/test_database_schema.py tests/integration/test_vector_ingestion.py -q; Remove-Item Env:RUN_DATABASE_TESTS`

运行：`uv run alembic downgrade 20260714_04; uv run alembic upgrade head`

预期：升级与降级成功；新入库片段有 `search_text`；旧片段保持可查询且可通过重处理补齐。

- [x] **Step 5：提交**

提交：`git commit -m "feat: 为文档片段增加全文检索索引"`

## Task 3：关键词 Retriever

**推荐模型：Sol**

**只读取：** `backend/app/rag/retriever.py`、`backend/app/rag/schemas.py`、`backend/app/db/models/document_chunk.py`、`backend/tests/integration/test_vector_retriever.py`。

**文件：**

- 新建：`backend/app/rag/contracts.py`
- 新建：`backend/app/rag/keyword_retriever.py`
- 新建：`backend/tests/integration/test_keyword_retriever.py`

**接口：**

```python
class Retriever(Protocol):
    async def search(
        self, *, knowledge_base_id: UUID, query: str,
        query_embedding: list[float], top_k: int,
        score_threshold: float,
    ) -> list[RetrievedChunk]: ...

class KeywordRetriever:
    async def search(
        self, *, knowledge_base_id: UUID, query: str,
        query_embedding: list[float], top_k: int,
        score_threshold: float,
    ) -> list[RetrievedChunk]: ...
```

- [x] **Step 1：写精确编号、排序、限制和跨知识库隔离测试**
- [x] **Step 2：将安全 Token 用 `|` 连接后传给 `to_tsquery('simple', ...)` 查询**

`build_search_text` 只产生中文、ASCII 字母和数字 Token，因此可安全构造 `token1 | token2` 的 OR 查询；禁止把原始用户文本直接传给 `to_tsquery`。排序使用 `ts_rank_cd` 降序，再按 `DocumentChunk.id` 升序稳定排序。`query_embedding` 和 `score_threshold` 为统一协议参数，在关键词实现中不使用。

- [x] **Step 3：空 Token 查询直接返回空列表**
- [x] **Step 4：运行数据库测试并提交**

运行：`$env:RUN_DATABASE_TESTS='1'; uv run pytest tests/integration/test_keyword_retriever.py tests/integration/test_resource_permissions.py -q; Remove-Item Env:RUN_DATABASE_TESTS`

提交：`git commit -m "feat: 增加PostgreSQL关键词检索"`

## Task 4：RRF 与 HybridRetriever

**推荐模型：Sol**

**只读取：** `backend/app/rag/contracts.py`、两种 Retriever、`backend/app/rag/schemas.py`。

**文件：**

- 新建：`backend/app/rag/fusion.py`
- 新建：`backend/app/rag/hybrid_retriever.py`
- 新建：`backend/tests/unit/test_reciprocal_rank_fusion.py`
- 新建：`backend/tests/unit/test_hybrid_retriever.py`
- 修改：`backend/app/rag/retriever.py`
- 修改：`backend/app/evaluation/runner.py`
- 修改：`backend/tests/unit/test_evaluation_runner.py`

**接口：**

```python
def rrf_fuse(
    ranked_lists: list[list[RetrievedChunk]], *, top_k: int, rank_constant: int = 60,
) -> list[RetrievedChunk]: ...

class HybridRetriever:
    def __init__(self, vector: Retriever, keyword: Retriever, *, rank_constant: int = 60): ...
```

- [x] **Step 1：写去重、双路加分、稳定排序和 top_k 测试**

```python
def test_rrf_rewards_chunk_found_by_both_retrievers() -> None:
    fused = rrf_fuse([[vector_only, shared], [shared, keyword_only]], top_k=3)
    assert fused[0].chunk_id == shared.chunk_id
```

- [x] **Step 2：实现 RRF**

每个列表从 1 开始排名，分数累加 `1 / (rank_constant + rank)`；按融合分数降序、`str(chunk_id)` 升序；输出使用 `dataclasses.replace(chunk, relevance_score=fused_score)`。

- [x] **Step 3：VectorRetriever 和评估 Runner 接受并透传 `query`，向量 SQL 保持不变**
- [x] **Step 4：HybridRetriever 分别请求 `top_k` 个候选并融合**
- [x] **Step 5：运行单元测试并提交**

运行：`uv run pytest tests/unit/test_reciprocal_rank_fusion.py tests/unit/test_hybrid_retriever.py tests/unit/test_rag_service.py -q`

提交：`git commit -m "feat: 使用RRF融合向量与关键词检索"`

## Task 5：配置与 RagService 接线

**推荐模型：Sol**

**只读取：** `backend/app/core/config.py`、`backend/app/api/v1/questions.py`、`backend/app/rag/service.py`、相关测试。

**文件：**

- 修改：`backend/app/core/config.py`
- 修改：`backend/app/api/v1/questions.py`
- 修改：`backend/app/rag/service.py`
- 修改：`backend/tests/unit/test_config.py`
- 修改：`backend/tests/unit/test_rag_service.py`
- 修改：`backend/tests/integration/test_question_api.py`

**配置：**

```python
rag_retrieval_mode: Literal["vector", "hybrid"] = "vector"
rag_rrf_rank_constant: int = Field(default=60, ge=1, le=1000)
```

- [ ] **Step 1：写 vector/hybrid 工厂选择和 query 透传测试**
- [ ] **Step 2：让 RagService 通过 `Retriever` 协议接收实现**

`RagService.answer` 调用统一为：

```python
chunks = await self._retriever.search(
    knowledge_base_id=knowledge_base_id,
    query=question.strip(),
    query_embedding=query_embedding,
    top_k=top_k,
    score_threshold=self._score_threshold,
)
```

- [ ] **Step 3：依配置构造 VectorRetriever 或 HybridRetriever**
- [ ] **Step 4：运行 API、权限和回退测试**

运行：`uv run pytest tests/unit/test_config.py tests/unit/test_rag_service.py tests/integration/test_question_api.py tests/integration/test_resource_permissions.py -q`

预期：默认纯向量行为不变；hybrid 显式开启；跨知识库仍返回 404。

- [ ] **Step 5：提交**

提交：`git commit -m "feat: 接入可回退的混合检索"`

## Task 6：3B 指标验收

**推荐模型：Sol**

**只读取：** 当前阶段 diff、3A/3B 报告、本计划、执行看板。

**文件：**

- 修改：`docs/阶段3执行进度.md`
- 修改：`README.md`

- [ ] **Step 1：分别生成 vector 和 hybrid 报告**

```powershell
uv run python -m scripts.evaluate_rag --dataset tests/fixtures/evaluation/stage3.jsonl --knowledge-base-id $env:EVALUATION_KNOWLEDGE_BASE_ID --mode vector --output reports/stage3b-vector.json
uv run python -m scripts.evaluate_rag --dataset tests/fixtures/evaluation/stage3.jsonl --knowledge-base-id $env:EVALUATION_KNOWLEDGE_BASE_ID --mode hybrid --output reports/stage3b-hybrid.json
```

- [ ] **Step 2：检查质量门**

总体 Recall@5 不低于 3A；关键词分类 Recall@5 至少提升 10 个百分点。未达标时保持 3B `进行中`，记录失败数据，不进入 3C。

- [ ] **Step 3：运行完整测试和 Ruff**

运行命令与 3A Task 5 相同，预期全部通过。

- [ ] **Step 4：更新看板并提交**

通过后将 3B 标为 `已完成`、3C Task 1 标为 `进行中`，记录两份报告摘要、验证命令和提交标识。

提交：`git commit -m "docs: 完成阶段3B混合检索验收"`

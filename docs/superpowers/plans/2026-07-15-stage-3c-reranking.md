# 阶段 3C：Rerank 重排序实施计划

> **执行要求：** 使用 `executing-plans` 逐 Task 执行；除非用户明确要求，不派发子代理。所有步骤用本文复选框跟踪。

**目标：** 对混合检索候选片段进行本地 BGE 重排序，提高相关结果靠前程度，同时保留明确回退策略。

**架构：** `RerankerProvider` 只负责批量评分，`rerank_chunks` 负责稳定排序，`RagService` 负责候选数量、回退与最终 Top K。Fake Provider 保护普通测试，本地模型只在显式配置时加载。

**技术栈：** Python 3.12、sentence-transformers `CrossEncoder`、asyncio、pytest。

## 全局约束

- 3B 未验收通过时不得开始。
- 默认 `RAG_RERANKER_PROVIDER=disabled`，保持已有行为。
- 模型推理不得阻塞 FastAPI 事件循环，必须放入线程执行。
- 不允许静默回退；回退必须由配置允许并写结构化日志。
- 候选数必须大于等于最终 `top_k`。
- 普通测试只使用 Fake Provider，不下载模型。

---

## 文件职责图

| 文件 | 职责 |
|---|---|
| `backend/app/ai/contracts.py` | `RerankerProvider` 协议 |
| `backend/app/ai/rerankers.py` | Fake 与本地 CrossEncoder 实现 |
| `backend/app/rag/reranking.py` | 分数校验、稳定排序和 Top K |
| `backend/app/rag/service.py` | 候选召回、重排序和回退编排 |
| `backend/app/api/v1/questions.py` | 依配置构造 Provider |

## Task 1：Reranker 契约与稳定排序

**推荐模型：Terra**

**只读取：** `backend/app/ai/contracts.py`、`backend/app/rag/schemas.py`、本 Task。

**文件：**

- 修改：`backend/app/ai/contracts.py`
- 新建：`backend/app/rag/reranking.py`
- 新建：`backend/tests/unit/test_reranking.py`

**接口：**

```python
class RerankerProvider(Protocol):
    async def rerank(self, query: str, documents: list[str]) -> list[float]: ...

async def rerank_chunks(
    provider: RerankerProvider,
    *,
    query: str,
    chunks: list[RetrievedChunk],
    top_k: int,
) -> list[RetrievedChunk]: ...
```

- [x] **Step 1：写分数排序、同分稳定、长度不一致和 top_k 非法测试**

```python
async def test_rerank_chunks_replaces_score_and_orders_descending() -> None:
    result = await rerank_chunks(
        StubReranker([0.2, 0.9]), query="年假", chunks=[first, second], top_k=2
    )
    assert [item.chunk_id for item in result] == [second.chunk_id, first.chunk_id]
    assert result[0].relevance_score == 0.9
```

- [x] **Step 2：实现批量调用、长度校验和稳定排序**

使用 `dataclasses.replace` 写入新分数；排序键为 `(-score, 原候选序号)`；Provider 返回数量不一致时抛出 `AppError(code="RERANKER_PROVIDER_ERROR", status_code=502)`。

- [x] **Step 3：验证并提交**

运行：`uv run pytest tests/unit/test_reranking.py -q`

提交：`git commit -m "feat: 定义Reranker契约与稳定排序"`

## Task 2：Fake 与本地 BGE Reranker

**推荐模型：Terra**

**只读取：** `backend/app/ai/embeddings.py`、`backend/app/core/event_loop.py`、`backend/tests/unit/test_embedding_providers.py`。

**文件：**

- 新建：`backend/app/ai/rerankers.py`
- 新建：`backend/tests/unit/test_reranker_providers.py`

**接口：**

```python
class FakeRerankerProvider:
    def __init__(self, scores: list[float] | None = None) -> None: ...
    async def rerank(self, query: str, documents: list[str]) -> list[float]: ...

class LocalBgeRerankerProvider:
    def __init__(self, *, model_name: str, device: str, batch_size: int) -> None: ...
    async def rerank(self, query: str, documents: list[str]) -> list[float]: ...
```

- [x] **Step 1：Fake Provider 写固定分数和默认递减分数测试**
- [x] **Step 2：给本地 Provider 注入假的 CrossEncoder，测试参数与线程执行**
- [x] **Step 3：实现惰性加载和 `asyncio.to_thread`**

本地预测输入固定为 `[[query, document], ...]`；`device=auto` 时按 CUDA 可用性解析为 `cuda` 或 `cpu`，不把字符串 `auto` 直接传给 CrossEncoder；空文档列表直接返回空列表；底层异常转换为 `RERANKER_PROVIDER_ERROR`，不暴露模型缓存路径或请求文本。

- [x] **Step 4：验证不下载模型的单元测试并提交**

运行：`uv run pytest tests/unit/test_reranker_providers.py -q`

提交：`git commit -m "feat: 增加Fake与本地BGE重排序"`

## Task 3：配置和依赖构造

**推荐模型：Terra**

**只读取：** `backend/app/core/config.py`、`backend/app/api/v1/questions.py`、对应配置测试。

**文件：**

- 修改：`backend/app/core/config.py`
- 修改：`backend/app/api/v1/questions.py`
- 修改：`backend/tests/unit/test_config.py`
- 新建：`backend/tests/unit/test_question_reranker_dependency.py`

**配置：**

```python
rag_reranker_provider: Literal["disabled", "fake", "local"] = "disabled"
rag_reranker_model: str = "BAAI/bge-reranker-base"
rag_reranker_device: Literal["auto", "cuda", "cpu"] = "auto"
rag_reranker_batch_size: int = Field(default=16, ge=1, le=256)
rag_candidate_k: int = Field(default=20, ge=1, le=100)
rag_reranker_allow_fallback: bool = True
```

- [x] **Step 1：写默认禁用、候选数下限和 Provider 选择测试**
- [x] **Step 2：校验 `rag_candidate_k >= rag_top_k_default`**
- [x] **Step 3：实现依赖，disabled 返回 `None`，Fake/Local 返回对应 Provider**
- [x] **Step 4：验证并提交**

运行：`uv run pytest tests/unit/test_config.py tests/unit/test_question_reranker_dependency.py -q`

提交：`git commit -m "feat: 配置可选Reranker依赖"`

## Task 4：RagService 候选重排序与回退

**推荐模型：Sol**

**只读取：** `backend/app/rag/service.py`、`backend/app/rag/reranking.py`、`backend/app/api/v1/questions.py`、RagService 测试。

**文件：**

- 修改：`backend/app/rag/service.py`
- 修改：`backend/app/api/v1/questions.py`
- 修改：`backend/tests/unit/test_rag_service.py`
- 修改：`backend/tests/integration/test_question_api.py`

**构造参数：**

```python
reranker: RerankerProvider | None
candidate_k: int
reranker_allow_fallback: bool
```

- [x] **Step 1：写禁用、启用、严格失败和允许回退四组测试**
- [x] **Step 2：启用时以 `max(top_k, candidate_k)` 召回候选**
- [x] **Step 3：Rerank 后截取请求的最终 `top_k`**
- [x] **Step 4：仅捕获 `RERANKER_PROVIDER_ERROR` 执行配置回退**

允许回退时使用原融合顺序的前 `top_k`；其他 AppError 不得被吞掉。日志只记录错误码、Provider 名和 request ID 可关联信息，不记录问题全文或片段内容。

- [x] **Step 5：运行主链路与 API 测试并提交**

运行：`uv run pytest tests/unit/test_rag_service.py tests/integration/test_question_api.py tests/integration/test_resource_permissions.py -q`

提交：`git commit -m "feat: 在RAG主链路接入可回退重排序"`

## Task 5：3C 指标与性能验收

**推荐模型：Sol**

**只读取：** 当前阶段 diff、3B/3C 报告、测试输出、执行看板。

**文件：**

- 修改：`backend/scripts/evaluate_rag.py`
- 修改：`backend/tests/unit/test_evaluate_rag_script.py`
- 修改：`docs/阶段3执行进度.md`
- 修改：`README.md`

- [x] **Step 1：增加 `--mode rerank` 并在报告记录候选数和模型名**
- [x] **Step 2：生成 3B 与 3C 对比报告**

运行：`uv run python -m scripts.evaluate_rag --dataset tests/fixtures/evaluation/stage3.jsonl --knowledge-base-id $env:EVALUATION_KNOWLEDGE_BASE_ID --mode rerank --output reports/stage3c-rerank.json`

- [x] **Step 3：已检查门槛，但未通过**

MRR@5 相对 3B 提升至少 5%，引用命中率不得下降；同时记录 CPU 环境下 P50/P95。未达标时不得进入 3D。

- [x] **Step 4：运行完整测试、Ruff 和数据库集成测试**
- [x] **Step 5：更新看板并提交**

通过后将 3C 标为 `已完成`。只有 2D 同步检查通过，3D 才能改为 `进行中`；否则 3D 标记为 `阻塞`。

提交：`git commit -m "docs: 完成阶段3C重排序验收"`

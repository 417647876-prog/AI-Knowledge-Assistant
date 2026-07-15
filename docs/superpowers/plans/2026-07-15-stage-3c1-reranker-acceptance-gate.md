# 阶段 3C.1 Reranker 接受门与安全拒答 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为成功完成的 BGE 重排增加独立低分接受门，并在没有合格候选时复用现有安全拒答，同时提供不污染阶段 3 固定验收集的阈值校准工具。

**Architecture:** 保持现有 `Retriever → Reranker → RagService → Evaluation` 分层：`rerank_chunks` 只负责评分与稳定排序，新纯函数只负责接受判定，`RagService` 负责把空结果转换为安全拒答。阈值通过独立校准数据离线生成，并作为显式环境配置注入；Provider 异常仍走原 fallback，而成功但低分不得 fallback。

**Tech Stack:** Python 3.12、Pydantic Settings 2、FastAPI、Sentence Transformers 5、pytest、Ruff。

## Global Constraints

- `rag_reranker_min_score` 的类型必须是 `float | None`，默认值必须是 `None`；`None` 表示关闭接受门。
- 只允许有限浮点数；`NaN`、`+Infinity`、`-Infinity` 必须在配置加载时被拒绝。
- 不得复用 `rag_score_threshold`；向量相似度与 CrossEncoder 分数属于不同数值空间。
- 接受规则固定为 `score >= min_score`，等于阈值的候选必须保留。
- 接受门只在 Reranker 成功返回有效分数后生效；Reranker 禁用时保持现有行为。
- Provider 执行失败继续遵循 `rag_reranker_allow_fallback`；Provider 成功但候选全部低分时必须安全拒答，禁止回退到原候选。
- 普通问答、流式问答和评估必须消费同一份最终候选列表。
- 校准集不得复用、修改或反向调参 `backend/tests/fixtures/evaluation/stage3.jsonl` 的问题文本。
- 校准首要约束为负样本错误接受率 `0`，次要约束为正样本接受率至少 `0.8`；无可行阈值时命令必须失败且不得推荐开启接受门。
- 不修改阶段 3C 原质量门，不弱化测试，不调整固定 30 条评估数据制造通过；原 MRR 相对提升不足 5% 时，Task 5、3C 和 3D 状态不得标记完成。
- 日志和报告不得包含问题全文、片段全文、原始分数列表、密钥或数据库连接串。

---

### Task 1: 有限阈值配置与纯接受函数

**Files:**
- Modify: `backend/app/core/config.py:42-47`
- Modify: `backend/app/rag/reranking.py:1-40`
- Test: `backend/tests/unit/test_config.py`
- Test: `backend/tests/unit/test_reranking.py`

**Interfaces:**
- Consumes: 已有 `RetrievedChunk.relevance_score: float`，分数已经由 `rerank_chunks` 做有限性校验。
- Produces: `Settings.rag_reranker_min_score: float | None`；`accept_reranked_chunks(chunks: list[RetrievedChunk], *, min_score: float | None) -> list[RetrievedChunk]`。

- [x] **Step 1: 写配置失败测试**

在 `test_settings_use_stage_3c_reranker_defaults` 增加默认关闭断言，并增加有限性参数化测试：

```python
assert settings.rag_reranker_min_score is None


@pytest.mark.parametrize("invalid_score", [float("nan"), float("inf"), float("-inf")])
def test_settings_reject_non_finite_reranker_min_score(invalid_score: float) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, rag_reranker_min_score=invalid_score)


def test_settings_accepts_finite_reranker_min_score() -> None:
    settings = Settings(_env_file=None, rag_reranker_min_score=-2.75)

    assert settings.rag_reranker_min_score == -2.75
```

- [x] **Step 2: 运行配置测试并确认 RED**

Run: `Set-Location backend; uv run pytest tests/unit/test_config.py -q`

Expected: FAIL，原因是 `Settings` 尚无 `rag_reranker_min_score`，或非有限值尚未被拒绝。

- [x] **Step 3: 添加最小配置实现**

在 Reranker 配置旁增加：

```python
rag_reranker_min_score: float | None = Field(default=None, allow_inf_nan=False)
```

- [x] **Step 4: 运行配置测试并确认 GREEN**

Run: `Set-Location backend; uv run pytest tests/unit/test_config.py -q`

Expected: PASS。

- [x] **Step 5: 写接受函数失败测试**

把 `accept_reranked_chunks` 加入导入，并添加四个行为测试：

```python
def test_accept_reranked_chunks_returns_all_when_gate_is_disabled() -> None:
    chunks = [make_chunk(1, score=-3.0), make_chunk(2, score=0.8)]

    assert accept_reranked_chunks(chunks, min_score=None) == chunks


def test_accept_reranked_chunks_keeps_score_equal_to_threshold() -> None:
    chunks = [make_chunk(1, score=0.4), make_chunk(2, score=0.3)]

    result = accept_reranked_chunks(chunks, min_score=0.4)

    assert [item.chunk_id for item in result] == [chunks[0].chunk_id]


def test_accept_reranked_chunks_preserves_accepted_order() -> None:
    chunks = [make_chunk(1, score=0.9), make_chunk(2, score=0.2), make_chunk(3, score=0.7)]

    result = accept_reranked_chunks(chunks, min_score=0.5)

    assert [item.chunk_id for item in result] == [chunks[0].chunk_id, chunks[2].chunk_id]


def test_accept_reranked_chunks_can_reject_every_chunk() -> None:
    assert accept_reranked_chunks([make_chunk(1, score=-1.0)], min_score=0.0) == []
```

- [x] **Step 6: 运行接受函数测试并确认 RED**

Run: `Set-Location backend; uv run pytest tests/unit/test_reranking.py -q`

Expected: collection ERROR 或 FAIL，原因是 `accept_reranked_chunks` 尚不存在。

- [x] **Step 7: 添加最小接受函数**

在 `rerank_chunks` 前增加：

```python
def accept_reranked_chunks(
    chunks: list[RetrievedChunk],
    *,
    min_score: float | None,
) -> list[RetrievedChunk]:
    if min_score is None:
        return chunks
    return [chunk for chunk in chunks if chunk.relevance_score >= min_score]
```

- [x] **Step 8: 运行 Task 1 聚焦测试与静态检查**

Run: `Set-Location backend; uv run pytest tests/unit/test_config.py tests/unit/test_reranking.py -q`

Expected: PASS。

Run: `Set-Location backend; uv run ruff check app/core/config.py app/rag/reranking.py tests/unit/test_config.py tests/unit/test_reranking.py; uv run ruff format --check app/core/config.py app/rag/reranking.py tests/unit/test_config.py tests/unit/test_reranking.py`

Expected: 两条命令均退出码 0。

- [x] **Step 9: 提交 Task 1**

```powershell
git add backend/app/core/config.py backend/app/rag/reranking.py backend/tests/unit/test_config.py backend/tests/unit/test_reranking.py
git commit -m "feat: 增加Reranker接受阈值纯函数"
```

---

### Task 2: RagService 接受门、安全拒答与运行时接线

**Files:**
- Modify: `backend/app/rag/service.py:34-96`
- Modify: `backend/app/api/v1/questions.py:172-197`
- Test: `backend/tests/unit/test_rag_service.py`
- Test: `backend/tests/unit/test_question_reranker_dependency.py`

**Interfaces:**
- Consumes: Task 1 的 `accept_reranked_chunks(chunks, min_score=min_score)` 和 `Settings.rag_reranker_min_score`。
- Produces: `RagService` 新增仅限关键字参数 `reranker_min_score: float | None = None`；普通和流式链路共享过滤后的 `_retrieve` 返回值。

- [x] **Step 1: 写部分接受和全部拒绝的失败测试**

在 `test_rag_service.py` 增加：

```python
@pytest.mark.asyncio
async def test_reranker_acceptance_gate_keeps_only_qualified_chunks() -> None:
    chunks = [_chunk(), _chunk(), _chunk()]
    service = RagService(
        session=FakeSession(object()),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=StubRetriever(chunks),
        chat_provider=CountingChatProvider("答案。[1]"),
        question_rewriter=RecordingRewriter("不应调用"),
        score_threshold=0.55,
        reranker=StubReranker(scores=[0.8, 0.49, -1.0]),
        candidate_k=3,
        reranker_allow_fallback=False,
        reranker_min_score=0.5,
    )

    answer, final_chunks, _ = await service.answer_with_retrieval(uuid4(), "年假", 3)

    assert [item.relevance_score for item in final_chunks] == [0.8]
    assert answer.retrieved_chunk_count == 1


@pytest.mark.asyncio
async def test_successful_low_score_rerank_refuses_without_fallback_or_chat() -> None:
    chunks = [_chunk(), _chunk()]
    chat = CountingChatProvider("不应该被调用")
    service = RagService(
        session=FakeSession(object()),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=StubRetriever(chunks),
        chat_provider=chat,
        question_rewriter=RecordingRewriter("不应调用"),
        score_threshold=0.55,
        reranker=StubReranker(scores=[-2.0, -3.0]),
        candidate_k=2,
        reranker_allow_fallback=True,
        reranker_min_score=0.0,
    )

    answer, final_chunks, _ = await service.answer_with_retrieval(uuid4(), "无关问题", 2)

    assert final_chunks == []
    assert answer.answer == "未找到足够依据，无法根据当前知识库回答该问题。"
    assert answer.citations == []
    assert answer.retrieved_chunk_count == 0
    assert chat.call_count == 0
```

- [x] **Step 2: 运行 RagService 测试并确认 RED**

Run: `Set-Location backend; uv run pytest tests/unit/test_rag_service.py -q`

Expected: FAIL，原因是构造函数尚不接受 `reranker_min_score`。

- [x] **Step 3: 实现过滤与脱敏结构化日志**

将导入改为：

```python
from app.rag.reranking import accept_reranked_chunks, rerank_chunks
```

构造函数增加并保存：

```python
reranker_min_score: float | None = None,

self._reranker_min_score = reranker_min_score
```

在 `_retrieve` 的 `try` 内先保存重排结果，再应用接受门：

```python
reranked_chunks = await rerank_chunks(
    self._reranker,
    query=question,
    chunks=chunks,
    top_k=min(top_k, len(chunks)),
)
accepted_chunks = accept_reranked_chunks(
    reranked_chunks,
    min_score=self._reranker_min_score,
)
if len(accepted_chunks) < len(reranked_chunks):
    logger.info(
        "Reranker 接受门已过滤低分候选。",
        extra={
            "reranker_provider": type(self._reranker).__name__,
            "reranker_min_score": self._reranker_min_score,
            "candidate_count": len(reranked_chunks),
            "accepted_count": len(accepted_chunks),
            "rejected_count": len(reranked_chunks) - len(accepted_chunks),
            "request_id": get_request_id(),
        },
    )
return accepted_chunks
```

异常捕获保持在上述逻辑外层，因此只有 Provider `AppError` 能进入 fallback；空接受结果直接返回。

- [x] **Step 4: 运行 RagService 测试并确认 GREEN**

Run: `Set-Location backend; uv run pytest tests/unit/test_rag_service.py -q`

Expected: PASS。

- [x] **Step 5: 写流式拒答和依赖接线失败测试**

增加流式行为测试，断言事件为 `rewrite → status(retrieving) → retrieval(0) → token → done`，并断言 `generation_ms == 0`、空引用和 `chat.call_count == 0`。在 `test_question_reranker_dependency.py` 的工厂测试增加：

```python
settings = Settings(
    _env_file=None,
    rag_reranker_provider="fake",
    rag_reranker_min_score=-0.25,
)

assert service._reranker_min_score == -0.25
```

- [x] **Step 6: 运行接线测试并确认 RED**

Run: `Set-Location backend; uv run pytest tests/unit/test_rag_service.py tests/unit/test_question_reranker_dependency.py -q`

Expected: 至少依赖接线断言 FAIL，因为 `get_rag_service` 尚未传入阈值。

- [x] **Step 7: 在 API 工厂注入配置**

在 `get_rag_service` 创建 `RagService` 时增加：

```python
reranker_min_score=settings.rag_reranker_min_score,
```

- [x] **Step 8: 验证 Task 2**

Run: `Set-Location backend; uv run pytest tests/unit/test_rag_service.py tests/unit/test_rag_streaming.py tests/unit/test_question_reranker_dependency.py -q`

Expected: PASS，且现有 Provider 失败 fallback 测试继续通过。

Run: `Set-Location backend; uv run ruff check app/rag/service.py app/api/v1/questions.py tests/unit/test_rag_service.py tests/unit/test_question_reranker_dependency.py; uv run ruff format --check app/rag/service.py app/api/v1/questions.py tests/unit/test_rag_service.py tests/unit/test_question_reranker_dependency.py`

Expected: 两条命令均退出码 0。

- [x] **Step 9: 提交 Task 2**

```powershell
git add backend/app/rag/service.py backend/app/api/v1/questions.py backend/tests/unit/test_rag_service.py backend/tests/unit/test_question_reranker_dependency.py
git commit -m "feat: 接入Reranker低分安全拒答"
```

---

### Task 3: 评估链路记录最终接受数量与阈值

**Files:**
- Modify: `backend/app/evaluation/schemas.py:45-68`
- Modify: `backend/app/evaluation/runner.py:80-134`
- Modify: `backend/scripts/evaluate_rag.py:104-125,170-190`
- Test: `backend/tests/unit/test_evaluation_runner.py`
- Test: `backend/tests/unit/test_evaluate_rag_script.py`

**Interfaces:**
- Consumes: Task 2 的 `RagService` 构造参数 `reranker_min_score` 和最终 `retrieved_chunks`。
- Produces: 每个 `CaseResult.accepted_chunk_count: int`；安全环境字段 `rag_reranker_min_score`，值为 `"disabled"` 或浮点数字符串。

- [x] **Step 1: 写评估报告失败测试**

在 rerank 最终链路测试中增加：

```python
assert report.cases[0].accepted_chunk_count == len(report.cases[0].retrieved_files)
```

在安全环境测试中增加：

```python
settings = Settings(_env_file=None, rag_reranker_min_score=-0.25)
environment = build_safe_environment(settings)

assert environment["rag_reranker_min_score"] == "-0.25"
```

另加默认关闭断言：

```python
assert build_safe_environment(Settings(_env_file=None))["rag_reranker_min_score"] == "disabled"
```

- [x] **Step 2: 运行评估测试并确认 RED**

Run: `Set-Location backend; uv run pytest tests/unit/test_evaluation_runner.py tests/unit/test_evaluate_rag_script.py -q`

Expected: FAIL，原因是报告案例和环境尚无新字段。

- [x] **Step 3: 增加报告字段并使用同一最终链路赋值**

在 `CaseResult` 增加：

```python
accepted_chunk_count: int = Field(ge=0)
```

在 `evaluate_cases` 构造 `CaseResult` 时增加：

```python
accepted_chunk_count=len(chunks),
```

这保证 `retrieved_files`、指标和接受数量来自同一 `chunks` 变量。

- [x] **Step 4: 增加安全环境元数据并注入评估服务**

在 `build_safe_environment` 增加：

```python
"rag_reranker_min_score": (
    "disabled"
    if settings.rag_reranker_min_score is None
    else str(settings.rag_reranker_min_score)
),
```

在 `run_from_args` 创建 `RagService` 时增加：

```python
reranker_min_score=evaluation_settings.rag_reranker_min_score,
```

- [x] **Step 5: 修正测试中直接构造的 CaseResult**

所有测试夹具均用真实最终数量显式填写，例如：

```python
accepted_chunk_count=1,
```

不得给生产 Schema 增加掩盖漏传的默认值。

- [x] **Step 6: 验证 Task 3**

Run: `Set-Location backend; uv run pytest tests/unit/test_evaluation_runner.py tests/unit/test_evaluate_rag_script.py -q`

Expected: PASS。

Run: `Set-Location backend; uv run ruff check app/evaluation/schemas.py app/evaluation/runner.py scripts/evaluate_rag.py tests/unit/test_evaluation_runner.py tests/unit/test_evaluate_rag_script.py; uv run ruff format --check app/evaluation/schemas.py app/evaluation/runner.py scripts/evaluate_rag.py tests/unit/test_evaluation_runner.py tests/unit/test_evaluate_rag_script.py`

Expected: 两条命令均退出码 0。

- [x] **Step 7: 提交 Task 3**

```powershell
git add backend/app/evaluation/schemas.py backend/app/evaluation/runner.py backend/scripts/evaluate_rag.py backend/tests/unit/test_evaluation_runner.py backend/tests/unit/test_evaluate_rag_script.py
git commit -m "feat: 记录重排接受门评估元数据"
```

---

### Task 4: 独立校准契约、阈值选择 CLI 与数据集

**Files:**
- Create: `backend/app/evaluation/reranker_calibration.py`
- Create: `backend/scripts/calibrate_reranker.py`
- Create: `backend/tests/unit/test_reranker_calibration.py`
- Create: `backend/tests/unit/test_calibrate_reranker_script.py`
- Create: `backend/tests/fixtures/evaluation/stage3c-reranker-calibration.jsonl`

**Interfaces:**
- Consumes: `get_local_reranker_provider(model_name, device, batch_size)` 与 `RerankerProvider.rerank(query, documents)`。
- Produces: `CalibrationCase`、`CalibrationReport`、`load_calibration_cases(path)`、`select_acceptance_threshold(cases, scores, model_name, device, dataset_sha256, min_positive_accept_rate=0.8)`；CLI 模块 `python -m scripts.calibrate_reranker`。

- [x] **Step 1: 写数据加载失败测试**

创建 `test_reranker_calibration.py`，覆盖有效 JSONL、重复 ID、非布尔 `relevant`、缺少正样本和缺少负样本：

```python
def test_load_calibration_cases_requires_positive_and_negative_samples(tmp_path: Path) -> None:
    dataset = tmp_path / "calibration.jsonl"
    dataset.write_text(
        '{"id":"positive-1","question":"年假期限","document":"年假五天","relevant":true}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="正样本和负样本"):
        load_calibration_cases(dataset)
```

有效案例断言文本会去除首尾空白、ID 保持唯一、顺序保持不变。

- [x] **Step 2: 运行加载测试并确认 RED**

Run: `Set-Location backend; uv run pytest tests/unit/test_reranker_calibration.py -q`

Expected: collection ERROR，因为校准模块尚不存在。

- [x] **Step 3: 实现校准 Schema 与加载器**

在新模块定义：

```python
class CalibrationCase(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9-]+$", min_length=1, max_length=100)
    question: str = Field(min_length=1, max_length=2000)
    document: str = Field(min_length=1, max_length=8000)
    relevant: bool


class CalibrationReport(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    model_name: str
    device: str
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_count: int = Field(ge=2)
    positive_count: int = Field(ge=1)
    negative_count: int = Field(ge=1)
    score_min: float
    score_max: float
    recommended_min_score: float
    false_accept_rate: float = Field(ge=0, le=1)
    positive_accept_rate: float = Field(ge=0, le=1)
```

`load_calibration_cases` 逐行 `model_validate_json`，拒绝空文件、重复 ID、缺少任一标签，并把 Pydantic 错误统一转换为不包含原文的 `ValueError("校准数据集格式无效")`。

- [x] **Step 4: 写阈值选择失败测试**

```python
def test_select_acceptance_threshold_chooses_lowest_feasible_midpoint() -> None:
    cases = make_cases([False, False, True, True, True, True, True])

    report = select_acceptance_threshold(
        cases,
        [-3.0, -1.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        model_name="BAAI/test",
        device="cpu",
        dataset_sha256="a" * 64,
    )

    assert report.recommended_min_score == pytest.approx(-0.4)
    assert report.false_accept_rate == 0.0
    assert report.positive_accept_rate == 1.0


def test_select_acceptance_threshold_fails_when_constraints_conflict() -> None:
    cases = make_cases([False, True, True, True, True, True])

    with pytest.raises(ValueError, match="不存在满足约束"):
        select_acceptance_threshold(
            cases,
            [0.9, 0.1, 0.2, 0.3, 0.4, 0.5],
            model_name="BAAI/test",
            device="cpu",
            dataset_sha256="b" * 64,
        )
```

同时覆盖分数数量不一致及 `NaN/Infinity`，错误文本不得包含分数或样本文本。

- [x] **Step 5: 运行阈值测试并确认 RED**

Run: `Set-Location backend; uv run pytest tests/unit/test_reranker_calibration.py -q`

Expected: FAIL，因为阈值选择函数尚不存在。

- [x] **Step 6: 实现最低可行中点搜索**

实现固定规则：

```python
unique_scores = sorted(set(normalized_scores))
candidate_thresholds = [
    (lower + upper) / 2
    for lower, upper in zip(unique_scores, unique_scores[1:], strict=False)
]
for threshold in candidate_thresholds:
    false_accept_rate = accepted_negative_count / negative_count
    positive_accept_rate = accepted_positive_count / positive_count
    if false_accept_rate == 0.0 and positive_accept_rate >= min_positive_accept_rate:
        return CalibrationReport(
            model_name=model_name,
            device=device,
            dataset_sha256=dataset_sha256,
            case_count=len(cases),
            positive_count=positive_count,
            negative_count=negative_count,
            score_min=min(normalized_scores),
            score_max=max(normalized_scores),
            recommended_min_score=threshold,
            false_accept_rate=false_accept_rate,
            positive_accept_rate=positive_accept_rate,
        )
raise ValueError("不存在满足约束的 Reranker 接受阈值")
```

其中接受判断必须调用与运行时相同的 `score >= threshold` 语义；`min_positive_accept_rate` 默认 `0.8`，且只接受 `[0, 1]`。中点用 `lower + (upper - lower) / 2` 计算，结果再次检查有限性。

- [x] **Step 7: 写 CLI 失败测试**

在 `test_calibrate_reranker_script.py` 使用 Stub Provider 验证：参数解析、按问题分组的批量评分、报告 UTF-8 JSON、无可行阈值时退出失败且不生成报告。核心调用形态：

```python
report = await run_calibration(
    dataset=dataset,
    model_name="BAAI/test-reranker",
    device="cpu",
    provider=StubReranker(scores=[-1.0, 1.0]),
)

assert report.false_accept_rate == 0.0
assert provider.calls == [
    ("年假怎么计算？", ["年假为五天。", "机房访客需要登记。"]),
]
```

同一问题的正负片段必须合并为一次 `rerank(question, documents)`，以便真实 CrossEncoder 批量推理；不同问题不得错误合并为同一个 query。返回分数按原案例索引还原，报告与数据哈希不受分组顺序影响。

- [x] **Step 8: 运行 CLI 测试并确认 RED**

Run: `Set-Location backend; uv run pytest tests/unit/test_calibrate_reranker_script.py -q`

Expected: collection ERROR，因为脚本尚不存在。

- [x] **Step 9: 实现校准 CLI**

参数固定为：

```text
--dataset PATH                 必填
--output PATH                  必填
--model BAAI/bge-reranker-base
--device {auto,cuda,cpu}       默认 cpu
--batch-size 16                范围 1..256
```

`run_calibration` 按 `question` 的首次出现顺序分组，对每组调用 `await provider.rerank(question, documents)`；每组返回数量必须与文档数量一致，所有分数必须有限，再按原案例索引还原一维分数列表。数据哈希使用校准文件原始字节的 SHA-256。`main` 只在成功后写报告，失败时输出脱敏错误并以非零状态退出。

- [x] **Step 10: 创建独立中文校准数据集**

创建至少 20 条问题—片段对，至少 10 个独立问题；每个问题同时提供一个正样本和一个负样本，正负样本都不少于 10 条，并覆盖：制度编号、语义改写、明显无关、词面相似但语义无关。问题文本不得与 `stage3.jsonl` 任一 `question` 完全相同；用测试读取两份文件并断言集合不相交：

```python
calibration_questions = {case.question for case in load_calibration_cases(calibration_path)}
stage3_questions = {
    json.loads(line)["question"]
    for line in stage3_path.read_text(encoding="utf-8").splitlines()
    if line.strip()
}
assert calibration_questions.isdisjoint(stage3_questions)
```

每条 `document` 只使用仓库测试文档中的非敏感制度内容，不写入用户数据。

- [x] **Step 11: 验证 Task 4**

Run: `Set-Location backend; uv run pytest tests/unit/test_reranker_calibration.py tests/unit/test_calibrate_reranker_script.py -q`

Expected: PASS，测试不得下载真实模型。

Run: `Set-Location backend; uv run python -m scripts.calibrate_reranker --help`

Expected: 退出码 0，显示上述五个参数。

Run: `Set-Location backend; uv run ruff check app/evaluation/reranker_calibration.py scripts/calibrate_reranker.py tests/unit/test_reranker_calibration.py tests/unit/test_calibrate_reranker_script.py; uv run ruff format --check app/evaluation/reranker_calibration.py scripts/calibrate_reranker.py tests/unit/test_reranker_calibration.py tests/unit/test_calibrate_reranker_script.py`

Expected: 两条命令均退出码 0。

- [x] **Step 12: 提交 Task 4**

```powershell
git add backend/app/evaluation/reranker_calibration.py backend/scripts/calibrate_reranker.py backend/tests/unit/test_reranker_calibration.py backend/tests/unit/test_calibrate_reranker_script.py backend/tests/fixtures/evaluation/stage3c-reranker-calibration.jsonl
git commit -m "feat: 增加独立Reranker阈值校准工具"
```

---

### Task 5: 真实校准、固定评估回归与中文文档收口

**Files:**
- Modify: `README.md`
- Modify: `docs/阶段3执行进度.md`
- Modify: `docs/superpowers/plans/2026-07-15-stage-3c1-reranker-acceptance-gate.md`
- Runtime output, ignored by Git: `backend/reports/stage3c1-reranker-calibration.json`
- Runtime output, ignored by Git: `backend/reports/stage3c1-rerank-threshold.json`

**Interfaces:**
- Consumes: Task 4 校准 CLI 推荐的显式阈值、既有评估知识库 `f1279eb3-dcfd-490e-ad33-973e23df5e5e`、固定 `stage3.jsonl`。
- Produces: 可复现的本地校准报告、阈值开启后的固定评估报告、中文运行说明与不夸大的阶段状态。

- [x] **Step 1: 运行全部非数据库测试**

Run: `Set-Location backend; uv run pytest -q`

Expected: 所有单元测试通过；数据库集成测试按现有标记跳过。

- [x] **Step 2: 用真实 CPU BGE 生成独立校准报告**

Run:

```powershell
Set-Location backend
uv run python -m scripts.calibrate_reranker `
  --dataset tests/fixtures/evaluation/stage3c-reranker-calibration.jsonl `
  --model BAAI/bge-reranker-base `
  --device cpu `
  --batch-size 16 `
  --output reports/stage3c1-reranker-calibration.json
```

Expected: 成功时报告 `false_accept_rate == 0.0`、`positive_accept_rate >= 0.8` 并给出有限 `recommended_min_score`。若不存在可行阈值，记录真实失败，保持配置关闭，不继续执行启用阈值的评估命令。

- [x] **Step 3: 显式启用校准阈值重跑固定 30 条评估**

从报告读取有限阈值后仅设置当前 PowerShell 进程环境：

```powershell
$calibration = Get-Content reports/stage3c1-reranker-calibration.json -Encoding utf8 -Raw | ConvertFrom-Json
$env:RAG_RERANKER_MIN_SCORE = [string]$calibration.recommended_min_score
try {
  uv run python -m scripts.evaluate_rag `
    --dataset tests/fixtures/evaluation/stage3.jsonl `
    --knowledge-base-id f1279eb3-dcfd-490e-ad33-973e23df5e5e `
    --mode rerank `
    --top-k 5 `
    --output reports/stage3c1-rerank-threshold.json
}
finally {
  Remove-Item Env:RAG_RERANKER_MIN_SCORE -ErrorAction SilentlyContinue
}
```

Expected: 报告环境记录相同阈值，每个案例的 `accepted_chunk_count` 与最终 `retrieved_files` 数量一致。只报告实际指标；若 MRR 相对 3B 仍低于 5%，不得把阶段 3C 标为通过。

- [x] **Step 4: 用临时空库运行全部数据库集成测试**

Run:

```powershell
Set-Location (git rev-parse --show-toplevel)
docker compose -f deploy/docker-compose.yml up -d
$testDatabase = "knowledge_stage3c1_integration_test"
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

Expected: Alembic 从空库升级到 head，全部 integration 通过，临时库在 finally 删除。

- [x] **Step 5: 运行最终静态检查**

Run: `Set-Location backend; uv run ruff check app tests migrations scripts; uv run ruff format --check app tests migrations scripts`

Expected: 两条命令均退出码 0。

Run: `Set-Location (git rev-parse --show-toplevel); git diff --check`

Expected: 退出码 0。

- [x] **Step 6: 更新 README 与阶段进度**

README 增加中文说明：默认关闭、环境变量名、离线校准命令、成功低分不 fallback、全拒绝复用安全拒答，以及 BGE 原始分数不是概率。

`docs/阶段3执行进度.md` 新增“3C.1 生产安全增强”记录，写入真实测试数量、校准约束、推荐阈值、固定 30 条指标和性能。3C 原 Task 5 只在原 MRR 门真实通过时勾选；否则继续保持 3D 阻塞。

- [x] **Step 7: 重新运行文档改动后的最终门**

Run: `Set-Location backend; uv run pytest -q; uv run ruff check app tests migrations scripts; uv run ruff format --check app tests migrations scripts`

Expected: 全部退出码 0。

Run: `Set-Location (git rev-parse --show-toplevel); git diff --check; git status --short`

Expected: `git diff --check` 退出码 0；状态只包含本 Task 预期文档改动。

- [x] **Step 8: 提交 Task 5**

```powershell
git add README.md docs/阶段3执行进度.md docs/superpowers/plans/2026-07-15-stage-3c1-reranker-acceptance-gate.md
git commit -m "docs: 记录Reranker接受门真实验收"
```

- [ ] **Step 9: 推送当前功能分支**

Run: `git push origin codex/stage-3c-reranking`

Expected: 推送成功；不得改推 `main`，不得自动创建 PR 或合并。

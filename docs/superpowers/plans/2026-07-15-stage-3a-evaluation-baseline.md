# 阶段 3A：RAG 评估数据集与质量基线实施计划

> **执行要求：** 使用 `executing-plans` 逐 Task 执行；除非用户明确要求，不派发子代理。所有步骤用本文复选框跟踪。

**目标：** 建立可重复的中文 RAG 评估数据集、指标计算器和纯向量基线报告。

**架构：** `evaluation` 模块负责加载 JSONL、计算指标和组织评估，不修改线上问答 API。评估脚本复用现有数据库、EmbeddingProvider 和 VectorRetriever；普通测试只用 Fake Provider，真实模型必须显式启用。

**技术栈：** Python 3.12、Pydantic、pytest、SQLAlchemy asyncio、PostgreSQL、pgvector。

## 全局约束

- 设计依据：`docs/superpowers/specs/2026-07-15-stage-3-retrieval-quality-design.md`。
- 当前阶段只测量现有纯向量链路，不实现混合检索、Rerank 或 Query Rewrite。
- 数据集不得包含账号、Token、API Key 或企业真实敏感内容。
- `expected_sources` 使用稳定的文件名和关键文本，不使用每次入库都会变化的 UUID。
- 普通 `pytest` 不下载模型、不访问网络、不要求 PostgreSQL。
- 每个 Task 完成后更新 `docs/阶段3执行进度.md`。

---

## 文件职责图

| 文件 | 职责 |
|---|---|
| `backend/app/evaluation/schemas.py` | 评估样本、结果和汇总数据结构 |
| `backend/app/evaluation/dataset.py` | 严格加载、校验 JSONL |
| `backend/app/evaluation/metrics.py` | Recall、MRR、引用和拒答指标纯函数 |
| `backend/app/evaluation/runner.py` | 逐条执行检索并记录耗时 |
| `backend/scripts/evaluate_rag.py` | 命令行入口和 JSON 报告输出 |
| `backend/tests/fixtures/evaluation/stage3.jsonl` | 至少 30 条中文评估数据 |
| `backend/tests/unit/test_evaluation_*.py` | 不依赖数据库的单元测试 |
| `backend/tests/integration/test_evaluation_runner.py` | 显式启用的数据库基线测试 |
| `backend/reports/.gitkeep` | 保留报告目录，具体报告默认不提交 |

## Task 1：评估契约与数据集加载

**推荐模型：Terra**

**只读取：** 本计划、`backend/tests/fixtures/documents/README.md`、`backend/app/rag/schemas.py`。

**文件：**

- 新建：`backend/app/evaluation/__init__.py`
- 新建：`backend/app/evaluation/schemas.py`
- 新建：`backend/app/evaluation/dataset.py`
- 新建：`backend/tests/unit/test_evaluation_dataset.py`
- 新建：`backend/tests/fixtures/evaluation/stage3.jsonl`

**接口：**

```python
class ExpectedSource(BaseModel):
    file_name: str
    contains: str

class EvaluationTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str

class EvaluationCase(BaseModel):
    id: str
    category: Literal["keyword", "semantic", "refusal", "multi_turn", "interference"]
    question: str
    expected_sources: list[ExpectedSource]
    should_refuse: bool = False
    history: list[EvaluationTurn] = Field(default_factory=list)

class CaseResult(BaseModel):
    case_id: str
    retrieved_files: list[str]
    citation_files: list[str]
    recall_at_k: float
    reciprocal_rank: float
    refused: bool
    refusal_correct: bool
    latency_ms: float

class EvaluationReport(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["vector", "hybrid", "rerank", "rewrite"]
    dataset_sha256: str
    top_k: int
    case_count: int
    recall_at_5: float
    mrr_at_5: float
    citation_hit_rate: float
    refusal_accuracy: float
    latency_p50_ms: float
    latency_p95_ms: float
    environment: dict[str, str]
    cases: list[CaseResult]

def load_evaluation_cases(path: Path) -> list[EvaluationCase]: ...
```

- [x] **Step 1：先写加载成功、重复 ID、空文件和非法 JSON 的失败测试**

```python
def test_load_evaluation_cases_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        '{"id":"same","category":"refusal","question":"甲","expected_sources":[],"should_refuse":true}\n'
        '{"id":"same","category":"refusal","question":"乙","expected_sources":[],"should_refuse":true}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="重复评估 ID: same"):
        load_evaluation_cases(path)
```

- [x] **Step 2：运行测试，确认因模块不存在而失败**

运行：`Set-Location backend; uv run pytest tests/unit/test_evaluation_dataset.py -q`

预期：`FAIL`，错误包含 `No module named 'app.evaluation'`。

- [x] **Step 3：实现严格逐行加载和唯一 ID 校验**

```python
def load_evaluation_cases(path: Path) -> list[EvaluationCase]:
    cases: list[EvaluationCase] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        case = EvaluationCase.model_validate_json(raw_line)
        if case.id in seen:
            raise ValueError(f"重复评估 ID: {case.id}")
        seen.add(case.id)
        cases.append(case)
    if not cases:
        raise ValueError("评估数据集不能为空")
    return cases
```

- [x] **Step 4：建立 30 条数据并校验分类数量**

固定分布：`keyword=6`、`semantic=8`、`refusal=6`、`multi_turn=6`、`interference=4`。文件来源限定为现有 `01-年假制度.txt` 至 `05-远程办公指南.pdf`；拒答案例的 `expected_sources=[]` 且 `should_refuse=true`。

- [x] **Step 5：运行单元测试并提交**

运行：`uv run pytest tests/unit/test_evaluation_dataset.py -q`

预期：全部通过，加载结果恰好 30 条且 ID 唯一。

提交：`git commit -m "test: 建立阶段3评估数据集"`

## Task 2：检索质量指标

**推荐模型：Terra**

**只读取：** `backend/app/evaluation/schemas.py`、本 Task。

**文件：**

- 新建：`backend/app/evaluation/metrics.py`
- 新建：`backend/tests/unit/test_evaluation_metrics.py`

**接口：**

```python
def recall_at_k(
    expected: list[ExpectedSource], actual: list[RetrievedChunk], k: int
) -> float: ...
def reciprocal_rank_at_k(
    expected: list[ExpectedSource], actual: list[RetrievedChunk], k: int
) -> float: ...
def citation_hit_rate(
    expected: list[ExpectedSource], citations: list[Citation]
) -> float: ...
def refusal_is_correct(*, should_refuse: bool, refused: bool) -> bool: ...
def percentile(values: list[float], quantile: float) -> float: ...
```

- [x] **Step 1：写命中、未命中、多相关来源和空期望集合测试**

```python
def test_recall_and_mrr_use_ranked_sources() -> None:
    actual = [unrelated_chunk, annual_leave_chunk, handbook_chunk]
    expected = [
        ExpectedSource(file_name="年假制度.txt", contains="五天年假"),
        ExpectedSource(file_name="员工手册.docx", contains="年休假"),
    ]
    assert recall_at_k(expected, actual, 2) == 0.5
    assert reciprocal_rank_at_k(expected, actual, 3) == 0.5
```

- [x] **Step 2：确认失败后实现纯函数**

匹配条件固定为文件名相等且 `contains` 出现在片段内容中。空期望集合用于拒答样本，检索 Recall 和 MRR 返回 `1.0` 当且仅当实际结果也为空，否则返回 `0.0`。`k < 1` 必须抛出 `ValueError`。

- [x] **Step 3：验证边界并提交**

运行：`uv run pytest tests/unit/test_evaluation_metrics.py -q`

预期：全部通过。

提交：`git commit -m "feat: 增加RAG评估指标"`

## Task 3：评估 Runner

**推荐模型：Sol**

**只读取：** `backend/app/evaluation/*`、`backend/app/rag/retriever.py`、`backend/app/ai/contracts.py`。

**文件：**

- 修改：`backend/app/evaluation/schemas.py`
- 新建：`backend/app/evaluation/runner.py`
- 新建：`backend/tests/unit/test_evaluation_runner.py`

**接口：**

```python
class EvaluationRetriever(Protocol):
    async def search(
        self, *, knowledge_base_id: UUID, query_embedding: list[float],
        top_k: int, score_threshold: float,
    ) -> list[RetrievedChunk]: ...

class EvaluationAnswerer(Protocol):
    async def answer_case(
        self, *, knowledge_base_id: UUID, case: EvaluationCase, top_k: int
    ) -> QuestionAnswer: ...

async def evaluate_cases(
    *, cases: list[EvaluationCase], knowledge_base_id: UUID,
    embedding_provider: EmbeddingProvider, retriever: EvaluationRetriever,
    answerer: EvaluationAnswerer, top_k: int, score_threshold: float,
    mode: Literal["vector", "hybrid", "rerank", "rewrite"] = "vector",
    environment: dict[str, str] | None = None,
) -> EvaluationReport: ...
```

Runner 对校验后的案例按固定 JSON 表示计算 `dataset_sha256`，并把 `mode` 和脱敏后的
`environment` 写入报告。这样单元测试不依赖数据集文件路径，CLI 仍能输出可复现元数据。

- [x] **Step 1：用 Stub Retriever 写顺序、参数和耗时非负测试**
- [x] **Step 2：确认失败后实现逐案例 Embedding 与检索**

Runner 使用 `time.perf_counter()` 记录检索链路耗时。检索指标使用 Retriever 的完整候选；引用和拒答指标使用 `EvaluationAnswerer` 返回的 `QuestionAnswer`。相同文件的多个片段保持原排名，相关性由“文件名 + 关键文本”共同判断。3A 的 Answerer 使用 Fake ChatProvider，真实 Chat 模型不进入普通测试。

- [x] **Step 3：运行评估模块测试**

运行：`uv run pytest tests/unit/test_evaluation_dataset.py tests/unit/test_evaluation_metrics.py tests/unit/test_evaluation_runner.py -q`

预期：全部通过且无网络访问。

- [x] **Step 4：提交**

提交：`git commit -m "feat: 增加可复现检索评估Runner"`

## Task 4：纯向量基线 CLI 与数据库验证

**推荐模型：Terra**

**只读取：** `backend/scripts/smoke_test.py`、`backend/app/db/session.py`、`backend/app/evaluation/*`、`backend/tests/integration/test_vector_retriever.py`。

**文件：**

- 新建：`backend/scripts/evaluate_rag.py`
- 新建：`backend/tests/unit/test_evaluate_rag_script.py`
- 新建：`backend/tests/integration/test_evaluation_runner.py`
- 新建：`backend/reports/.gitkeep`
- 修改：`.gitignore`

**CLI：**

```powershell
uv run python -m scripts.evaluate_rag `
  --dataset tests/fixtures/evaluation/stage3.jsonl `
  --knowledge-base-id $env:EVALUATION_KNOWLEDGE_BASE_ID `
  --mode vector `
  --output reports/stage3a-vector-baseline.json
```

- [ ] **Step 1：写参数解析、脱敏错误和报告 schema 测试**
- [ ] **Step 2：实现 `--mode vector`，拒绝未知模式**

报告固定包含：`schema_version`、`mode`、`dataset_sha256`、`top_k`、`case_count`、`recall_at_5`、`mrr_at_5`、`citation_hit_rate`、`refusal_accuracy`、`latency_p50_ms`、`latency_p95_ms`、`environment`、`cases`。不得写数据库 URL、模型 Key 或完整认证信息。

- [ ] **Step 3：增加显式数据库集成测试**

测试创建临时用户、知识库、文档和向量片段，验证其他知识库高分片段不会进入评估结果，最后按 owner 删除临时数据。

- [ ] **Step 4：运行验证**

运行：`uv run pytest tests/unit/test_evaluate_rag_script.py -q`

运行：`$env:RUN_DATABASE_TESTS='1'; uv run pytest tests/integration/test_evaluation_runner.py -q; Remove-Item Env:RUN_DATABASE_TESTS`

预期：全部通过；报告目录只跟踪 `.gitkeep`，生成的 `*.json` 被忽略。

- [ ] **Step 5：提交**

提交：`git commit -m "feat: 输出纯向量检索基线"`

## Task 5：阶段验收与进度更新

**推荐模型：Sol**

**只读取：** 当前 Git diff、本计划、总体设计、测试输出、`docs/阶段3执行进度.md`。

**文件：**

- 修改：`docs/阶段3执行进度.md`
- 修改：`README.md`

- [ ] **Step 1：运行完整质量门**

```powershell
Set-Location backend
uv run pytest -q
uv run ruff check app tests migrations scripts
uv run ruff format --check app tests migrations scripts
$env:RUN_DATABASE_TESTS='1'
uv run pytest tests/integration -q
Remove-Item Env:RUN_DATABASE_TESTS
```

预期：全部通过。

- [ ] **Step 2：Sol 只审查阶段 diff、接口和失败回退**

运行：`git diff (git merge-base HEAD main)..HEAD -- backend/app/evaluation backend/scripts/evaluate_rag.py backend/tests docs README.md`

- [ ] **Step 3：更新看板证据**

记录 30 条数据分类数量、基线报告路径、四条验证命令结果和提交标识；将 3A 标为 `已完成`，将 3B Task 1 标为 `进行中`。

- [ ] **Step 4：提交**

提交：`git commit -m "docs: 完成阶段3A评估基线验收"`

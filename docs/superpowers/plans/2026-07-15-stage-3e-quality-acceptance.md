# 阶段 3E：RAG 质量综合验收 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在同一知识库快照上自动运行 `vector`、`hybrid`、`rerank`、`rewrite` 四种检索链路，重算阶段 3A～3E 质量门，并产出可复现、可脱敏提交的中文验收报告。

**Architecture:** 保留现有 `scripts.evaluate_rag` 单模式 CLI，在报告链路中增加 schema 1.1 溯源和知识库快照；新增策略、比较和 Markdown 渲染三个职责单一的模块。独立 `scripts.accept_stage3` 负责四模式顺序编排、每次运行前后快照复核、原子输出、manifest 和退出码，最后由真实环境验收完成第三阶段收口。

**Tech Stack:** Python 3.12、Pydantic 2、SQLAlchemy 2 AsyncSession、PostgreSQL 16、pgvector、pytest/pytest-asyncio、Ruff、PowerShell、Vue 3、Vitest、Vite。

## Global Constraints

- 只在 `D:\学习\AI-Knowledge-Assistant\.worktrees\stage-3e-quality-acceptance` 工作，分支固定为 `codex/stage-3e-quality-acceptance`；不得修改主工作区。
- 实施基线固定为阶段 3D 完成提交 `f6aead3`；设计事实来源固定为 `docs/superpowers/specs/2026-07-15-stage-3e-quality-acceptance-design.md`。
- 3E 拆分为 5 个功能 Task，开始 Task 1 时将第三阶段总数从 25 调整为 26；计划、设计和模型讨论不计入功能 Task。
- Task 1～5 的执行模型依次为 `Sol｜high`、`Sol｜xhigh`、`Terra｜medium`、`Sol｜xhigh`、`Sol｜xhigh`。
- 采用 TDD：每个功能行为先写失败测试并确认失败原因，再写最小实现；每个 Task 通过聚焦测试、Ruff 和 `git diff --check` 后独立提交。
- 现有 `scripts.evaluate_rag --mode vector|hybrid|rerank|rewrite` 参数和行为保持兼容；新增阶段级入口必须是 `scripts.accept_stage3`。
- 3E 最终绝对门只应用于 `rewrite`：Recall@5 ≥ 0.85、引用命中率 ≥ 0.90、拒答准确率 ≥ 0.90。
- `vector`、`hybrid`、`rerank` 是演进证据，不因 `vector` 低于 0.85 自动判定 3E 失败。
- 3C 必须显示“质量门未通过、已获风险豁免”；豁免只允许 MRR 相对提升不小于 0、rerank MRR 不低于 hybrid 且引用不退化。负增长或引用下降时豁免无效。
- 3B 关键词门和 3D 多轮门必须复用 `app.evaluation.metrics.ceiling_aware_target`；不得复制 Recall、MRR、引用或上限感知公式。
- 四份新报告必须是 schema 1.1，共享同一 `run_id`、知识库、快照、数据集、Top K、案例集合和公共环境。
- 每种模式运行前后都验证 `before_mode == S0 == after_mode`；输入、schema、策略、环境或快照错误返回 1，且不覆盖上一组正式产物。
- 质量失败返回 2，但仍生成标记为“未通过”的原始报告、中文报告和 manifest；成功返回 0。
- 原始 JSON 和 manifest 固定写入 `backend/reports` 并继续由 Git 忽略；正式 Markdown 固定为 `docs/阶段3质量验收报告.md`。
- Markdown、控制台和比较错误不得包含问题全文、片段全文、完整 Prompt、数据库连接信息、密钥或令牌；只允许列出失败案例 ID。
- 输出先写到编排器创建的临时目录；正式文件逐个使用 `os.replace`，manifest 最后替换。编排器只清理自己的临时目录。
- 单元测试使用 Fake/Stub Provider，不下载真实模型；只有 Task 5 使用真实本地 Embedding、DeepSeek Chat 和本地 BGE Reranker。
- 继续复用阶段 3D 对 `QUESTION_REWRITE_ERROR` 的精确原问题回退；3E 不实现第二套问题改写或回退逻辑。
- 不修改固定 30 条评估数据制造通过，不更换 Provider，不改 HTTP/SSE 契约，不开发阶段 4、Agent、多 Agent、训练或线上评测平台。
- 开始和完成每个 Task 都更新 `docs/阶段3执行进度.md`；只有真实门禁与全部验证通过后，才能把 3E 和第三阶段标为“已完成”。

---

## 文件职责图

| 文件 | 动作 | 单一职责 |
|---|---|---|
| `backend/app/evaluation/snapshot.py` | 新建 | 对当前知识库检索语料生成确定性 SHA-256 快照 |
| `backend/app/evaluation/schemas.py` | 修改 | 兼容报告 1.0/1.1，定义分类、逐案例引用和溯源模型 |
| `backend/app/evaluation/runner.py` | 修改 | 把案例分类与逐案例引用写进 `CaseResult` |
| `backend/scripts/evaluate_rag.py` | 修改 | 保持单模式 CLI，运行前后验证快照并生成报告 1.1 |
| `backend/app/evaluation/policy.py` | 新建 | 加载并校验版本化阶段 3 质量策略与 3C 豁免 |
| `backend/config/evaluation/stage3-quality-policy.json` | 新建 | 保存固定门槛、必需分类和受限豁免 |
| `backend/app/evaluation/comparison.py` | 新建 | 校验四报告兼容性，重算指标差异和 3A～3E 门 |
| `backend/app/evaluation/reporting.py` | 新建 | 纯函数渲染脱敏中文 Markdown 并扫描敏感标记 |
| `backend/scripts/accept_stage3.py` | 新建 | 四模式编排、原子输出、manifest、摘要和退出码 |
| `backend/tests/unit/test_evaluation_schemas.py` | 新建 | 报告 1.0/1.1 向后兼容和严格校验 |
| `backend/tests/unit/test_evaluation_snapshot.py` | 新建 | 快照规范化、排序和字段变化 |
| `backend/tests/integration/test_evaluation_snapshot.py` | 新建 | PostgreSQL 快照边界与知识库隔离 |
| `backend/tests/unit/test_evaluation_policy.py` | 新建 | 策略版本、比例、gate、证据和豁免 |
| `backend/tests/unit/test_evaluation_comparison.py` | 新建 | 可比性矩阵、分类指标、历史门、最终门和豁免 |
| `backend/tests/unit/test_evaluation_reporting.py` | 新建 | 章节、顺序、格式、脱敏和失败 ID |
| `backend/tests/unit/test_accept_stage3_script.py` | 新建 | 编排顺序、快照、原子输出、manifest 和退出码 |
| `backend/tests/unit/test_evaluation_runner.py` | 修改 | 断言分类和逐案例引用进入结果 |
| `backend/tests/unit/test_evaluate_rag_script.py` | 修改 | 断言报告 1.1、前后快照与旧 CLI 兼容 |
| `docs/阶段3质量验收报告.md` | Task 5 生成 | 可提交的脱敏中文验收证据 |
| `docs/阶段3验证与演示.md` | Task 5 新建 | 一键命令、五分钟演示和纯向量回退 |
| `docs/阶段3执行进度.md` | 每 Task 修改 | 5 个 Task、总数 26、命令、指标和提交证据 |
| `README.md` | Task 5 修改 | 阶段 3 一键验收入口和报告位置 |
| `docs/学习笔记.md` | Task 5 修改 | 面向求职复盘快照、质量门、豁免和回退 |

---

### Task 1：报告 1.1 溯源与知识库快照

**模型：** `Sol｜high`

**Files:**

- Create: `backend/app/evaluation/snapshot.py`
- Create: `backend/tests/unit/test_evaluation_schemas.py`
- Create: `backend/tests/unit/test_evaluation_snapshot.py`
- Create: `backend/tests/integration/test_evaluation_snapshot.py`
- Modify: `backend/app/evaluation/schemas.py`
- Modify: `backend/app/evaluation/runner.py`
- Modify: `backend/scripts/evaluate_rag.py`
- Modify: `backend/tests/unit/test_evaluation_runner.py`
- Modify: `backend/tests/unit/test_evaluate_rag_script.py`
- Modify: `docs/阶段3执行进度.md`
- Modify: `docs/superpowers/plans/2026-07-15-stage-3e-quality-acceptance.md`

**Interfaces:**

- Consumes: `EvaluationCase.category`、`citation_hit_rate(expected_sources, citations)`、`session_factory`、`Document`、`DocumentChunk`、`EvaluationReport`。
- Produces: `KnowledgeBaseSnapshot`、`EvaluationProvenance`、`compute_knowledge_base_snapshot(session, knowledge_base_id)`，以及带可选 `run_id` 和 `expected_snapshot` 的 `run_from_args`。

- [x] **Step 1：把 3E 标为进行中并锁定 5 Task 口径**

在 `docs/阶段3执行进度.md` 精确更新：

- 总进度 `21 / 26 Task`。
- 当前阶段 `3E：综合评估与验收（进行中）`。
- 当前 Task `3E Task 1：报告溯源与知识库快照`。
- 推荐/实际模型 `Sol｜high`。
- 阶段总览 3E 为 `0 / 5`，列表改成设计文档的 5 个 Task。

Run:

```powershell
rg -n "21 / 26|3E：综合评估与验收|0 / 5|报告溯源与知识库快照" docs/阶段3执行进度.md
```

Expected: 四项均命中，3A～3D 和 3C 豁免文字不变。

- [x] **Step 2：先写报告 schema 失败测试**

创建 `backend/tests/unit/test_evaluation_schemas.py`，先定义完整工厂：

```python
def make_case(**updates: object) -> CaseResult:
    values: dict[str, object] = {
        "case_id": "keyword-01",
        "retrieved_files": ["员工手册.docx"],
        "citation_files": ["员工手册.docx"],
        "accepted_chunk_count": 1,
        "recall_at_k": 1.0,
        "reciprocal_rank": 1.0,
        "refused": False,
        "refusal_correct": True,
        "latency_ms": 2.0,
    }
    values.update(updates)
    return CaseResult.model_validate(values)


def make_provenance() -> EvaluationProvenance:
    return EvaluationProvenance(
        run_id=uuid4(),
        knowledge_base_id=uuid4(),
        snapshot_sha256="b" * 64,
        document_count=5,
        chunk_count=13,
        generated_at=datetime.now(UTC),
    )


def make_report(**updates: object) -> EvaluationReport:
    values: dict[str, object] = {
        "mode": "vector",
        "dataset_sha256": "a" * 64,
        "top_k": 5,
        "case_count": 1,
        "recall_at_5": 1.0,
        "mrr_at_5": 1.0,
        "citation_hit_rate": 1.0,
        "refusal_accuracy": 1.0,
        "latency_p50_ms": 2.0,
        "latency_p95_ms": 2.0,
        "cases": [make_case()],
    }
    values.update(updates)
    return EvaluationReport.model_validate(values)


def test_report_10_remains_readable_without_provenance() -> None:
    assert make_report().schema_version == "1.0"


def test_report_11_requires_provenance_category_and_case_citation() -> None:
    provenance = make_provenance()
    report = make_report(
        schema_version="1.1",
        provenance=provenance,
        cases=[make_case(category="keyword", citation_hit_rate=1.0)],
    )
    assert report.provenance == provenance
    with pytest.raises(ValidationError, match="1.1 报告必须包含溯源信息"):
        make_report(schema_version="1.1")
    with pytest.raises(ValidationError, match="1.1 案例必须包含 category"):
        make_report(schema_version="1.1", provenance=provenance)


def test_report_rejects_case_count_mismatch() -> None:
    with pytest.raises(ValidationError, match="case_count 必须等于 cases 数量"):
        make_report(case_count=2)
```

Run:

```powershell
Set-Location backend
uv run pytest tests/unit/test_evaluation_schemas.py -q
```

Expected: import FAIL，指出 `EvaluationProvenance` 尚不存在。

- [x] **Step 3：扩展 schema 并保持 1.0 可读**

在 `backend/app/evaluation/schemas.py` 增加：

```python
EvaluationCategory = Literal[
    "keyword", "semantic", "refusal", "multi_turn", "interference"
]
GateStatus = Literal["passed", "failed", "waived"]


class EvaluationProvenance(BaseModel):
    run_id: UUID
    knowledge_base_id: UUID
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    generated_at: datetime
```

`CaseResult` 新增可选 `category: EvaluationCategory | None = None` 和 `citation_hit_rate: float | None = Field(default=None, ge=0, le=1)`。`EvaluationReport.schema_version` 改为 `Literal["1.0", "1.1"]`，新增可选 `provenance`，并增加模型校验：

```python
@model_validator(mode="after")
def validate_report_contract(self) -> "EvaluationReport":
    if self.case_count != len(self.cases):
        raise ValueError("case_count 必须等于 cases 数量")
    if self.schema_version == "1.1":
        if self.provenance is None:
            raise ValueError("1.1 报告必须包含溯源信息")
        for case in self.cases:
            if case.category is None:
                raise ValueError("1.1 案例必须包含 category")
            if case.citation_hit_rate is None:
                raise ValueError("1.1 案例必须包含 citation_hit_rate")
    return self
```

把 `EvaluationCase.category` 的内联 Literal 替换为 `EvaluationCategory`。不要要求 1.0 报告具有新字段。

- [x] **Step 4：让 Runner 写入分类与逐案例引用**

先在 `backend/tests/unit/test_evaluation_runner.py` 增加：

```python
assert report.cases[0].category == "keyword"
assert report.cases[0].citation_hit_rate == 1.0
```

再把 `backend/app/evaluation/runner.py` 的 `CaseResult` 构造参数增加：

```python
category=case.category,
citation_hit_rate=citation_score,
```

Run:

```powershell
uv run pytest tests/unit/test_evaluation_schemas.py tests/unit/test_evaluation_runner.py -q
```

Expected: PASS，旧报告仍默认为 schema 1.0。

- [x] **Step 5：写确定性快照失败测试**

`backend/tests/unit/test_evaluation_snapshot.py` 用固定 UUID 构造两个 `Document` 和两个 `DocumentChunk`，测试：

- 输入文档/片段顺序反转，`KnowledgeBaseSnapshot` 完全相等。
- `content`、`content_hash`、`search_text`、任一引用定位字段、`extra_metadata` 或 `embedding` 改变，哈希改变。
- 时间戳改变，哈希不变。
- 两个可变字符串即使包含相同分隔符，也不会产生相同哈希。

Run:

```powershell
uv run pytest tests/unit/test_evaluation_snapshot.py -q
```

Expected: import FAIL，指出 `app.evaluation.snapshot` 不存在。

- [x] **Step 6：实现快照规范化**

`backend/app/evaluation/snapshot.py` 定义：

```python
@dataclass(frozen=True)
class KnowledgeBaseSnapshot:
    knowledge_base_id: UUID
    snapshot_sha256: str
    document_count: int
    chunk_count: int
```

实现规则：

1. 每个字段先编码字段名长度、字段名、值长度和值；长度分别使用网络字节序 `!I` 和 `!Q`。
2. 文本和 JSON 使用 UTF-8；JSON 固定 `ensure_ascii=False`、`sort_keys=True` 和紧凑分隔符。
3. Embedding 每个值用 `struct.pack("!d", float(value))`。
4. 文档按 UUID 排序，纳入 `id/original_file_name/file_hash/status`。
5. 片段按 `document_id/chunk_index/id` 排序，纳入 `id`、`document_id`、`chunk_index`、`content_hash`、`content`、`search_text`、`page_number`、`sheet_name`、`row_start`、`section_title`、`start_index`、`extra_metadata`、`embedding`。
6. `search_vector`、时间戳、知识库名称/描述/所有者不纳入。
7. `compute_knowledge_base_snapshot` 先确认知识库存在，再用 `knowledge_base_id` 过滤文档和片段，返回哈希与数量。

Run: `uv run pytest tests/unit/test_evaluation_snapshot.py -q`

Expected: PASS。

- [x] **Step 7：写 PostgreSQL 快照隔离测试**

`backend/tests/integration/test_evaluation_snapshot.py` 沿用现有 integration fixture 创建目标和其他两个知识库：

```python
before = await compute_knowledge_base_snapshot(session, target.id)
await change_chunk_content(other.id, "其他知识库变化")
after_other_change = await compute_knowledge_base_snapshot(session, target.id)
assert after_other_change == before

await change_chunk_content(target.id, "目标知识库变化")
after_target_change = await compute_knowledge_base_snapshot(session, target.id)
assert after_target_change.snapshot_sha256 != before.snapshot_sha256
```

fixture 的 `finally` 按 owner 删除测试知识库，依赖级联删除文档和片段。

Run:

```powershell
$env:RUN_DATABASE_TESTS = "1"
uv run pytest tests/integration/test_evaluation_snapshot.py -q
Remove-Item Env:RUN_DATABASE_TESTS
```

Expected: 已迁移测试库 PASS；未设置开关时 SKIP。

- [x] **Step 8：给单模式评测增加前后快照和 1.1 溯源**

在 `backend/tests/unit/test_evaluate_rag_script.py` 增加：

- 两次快照相同，报告为 1.1，`run_id` 和 `expected_snapshot` 被保留。
- 运行前与基准不一致，抛 `ValueError("知识库快照与本次验收基准不一致")`。
- 运行后变化，抛 `ValueError("知识库快照在评估期间发生变化")`。
- 不传新参数的旧 `run_evaluation_command` 仍成功。

`run_from_args` 签名固定为：

```python
async def run_from_args(
    args: argparse.Namespace,
    settings: Settings,
    *,
    run_id: UUID | None = None,
    expected_snapshot: KnowledgeBaseSnapshot | None = None,
) -> EvaluationReport:
```

在现有 session 中运行前计算 `before`，评估后计算 `after`。全部一致后用 `model_validate` 重新校验并返回，不能使用跳过验证的 `model_copy` 更新方式：

```python
return EvaluationReport.model_validate(
    {
        **report.model_dump(mode="python"),
        "schema_version": "1.1",
        "provenance": EvaluationProvenance(
            run_id=run_id or uuid4(),
            knowledge_base_id=args.knowledge_base_id,
            snapshot_sha256=before.snapshot_sha256,
            document_count=before.document_count,
            chunk_count=before.chunk_count,
            generated_at=datetime.now(UTC),
        ),
    },
)
```

- [x] **Step 9：验证 Task 1、更新看板并提交**

Run:

```powershell
Set-Location backend
uv run pytest tests/unit/test_evaluation_schemas.py tests/unit/test_evaluation_snapshot.py tests/unit/test_evaluation_runner.py tests/unit/test_evaluate_rag_script.py -q
uv run ruff check app tests migrations scripts
uv run ruff format --check app tests migrations scripts
Set-Location ..
git diff --check
```

Expected: 聚焦测试、Ruff 和 diff 全部通过；旧四模式参数测试继续通过。

看板更新为总进度 `22 / 26`、3E `1 / 5`，勾选 Task 1，记录真实测试数量，下一步为 Task 2/`Sol｜xhigh`。勾选本 Task 全部步骤。

Commit:

```powershell
git add backend/app/evaluation/snapshot.py backend/app/evaluation/schemas.py backend/app/evaluation/runner.py backend/scripts/evaluate_rag.py backend/tests/unit/test_evaluation_schemas.py backend/tests/unit/test_evaluation_snapshot.py backend/tests/integration/test_evaluation_snapshot.py backend/tests/unit/test_evaluation_runner.py backend/tests/unit/test_evaluate_rag_script.py docs/阶段3执行进度.md docs/superpowers/plans/2026-07-15-stage-3e-quality-acceptance.md
git commit -m "feat: 为评估报告增加知识库快照"
```

---

### Task 2：报告比较、质量策略与受限豁免

**模型：** `Sol｜xhigh`

**Files:**

- Create: `backend/app/evaluation/policy.py`
- Create: `backend/app/evaluation/comparison.py`
- Create: `backend/config/evaluation/stage3-quality-policy.json`
- Create: `backend/tests/unit/test_evaluation_policy.py`
- Create: `backend/tests/unit/test_evaluation_comparison.py`
- Modify: `backend/app/evaluation/metrics.py`
- Modify: `backend/tests/unit/test_evaluation_metrics.py`
- Modify: `docs/阶段3执行进度.md`
- Modify: `docs/superpowers/plans/2026-07-15-stage-3e-quality-acceptance.md`

**Interfaces:**

- Consumes: `EvaluationReport` 1.1、`EvaluationCategory`、`ceiling_aware_target`。
- Produces: `Stage3QualityPolicy`、`QualityWaiver`、`GateResult`、`Stage3Comparison`、`load_stage3_quality_policy` 和 `compare_stage3_reports`。

- [ ] **Step 1：先写相对提升边界测试**

在 `backend/tests/unit/test_evaluation_metrics.py` 增加：

```python
@pytest.mark.parametrize(
    ("baseline", "candidate", "expected"),
    [
        (0.8, 0.84, 0.05),
        (0.8, 0.8, 0.0),
        (0.8, 0.72, -0.1),
        (0.0, 0.0, 0.0),
        (0.0, 0.2, 1.0),
    ],
)
def test_relative_gain(baseline: float, candidate: float, expected: float) -> None:
    assert relative_gain(baseline, candidate) == pytest.approx(expected)


@pytest.mark.parametrize(("baseline", "candidate"), [(-0.1, 0.5), (0.5, 1.1)])
def test_relative_gain_rejects_invalid_metric(
    baseline: float,
    candidate: float,
) -> None:
    with pytest.raises(ValueError, match="0 到 1"):
        relative_gain(baseline, candidate)
```

Run:

```powershell
Set-Location backend
uv run pytest tests/unit/test_evaluation_metrics.py -q
```

Expected: import FAIL，指出 `relative_gain` 不存在。

- [ ] **Step 2：实现唯一的相对提升公式**

在 `backend/app/evaluation/metrics.py` 增加：

```python
def relative_gain(baseline: float, candidate: float) -> float:
    if not 0 <= baseline <= 1 or not 0 <= candidate <= 1:
        raise ValueError("baseline 和 candidate 必须位于 0 到 1 之间")
    if baseline == 0:
        return 0.0 if candidate == 0 else 1.0
    return (candidate - baseline) / baseline
```

Run: `uv run pytest tests/unit/test_evaluation_metrics.py -q`

Expected: PASS。

- [ ] **Step 3：写策略模型与加载失败测试**

创建 `backend/tests/unit/test_evaluation_policy.py`，先定义：

```python
def valid_policy_dict() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "final_mode": "rewrite",
        "minimum_case_count": 30,
        "required_categories": [
            "keyword",
            "semantic",
            "refusal",
            "multi_turn",
            "interference",
        ],
        "historical_thresholds": {
            "stage3b_keyword_gain": 0.10,
            "stage3c_mrr_relative_gain": 0.05,
            "stage3d_multi_turn_gain": 0.15,
        },
        "final_thresholds": {
            "recall_at_5": 0.85,
            "citation_hit_rate": 0.90,
            "refusal_accuracy": 0.90,
        },
        "waivers": [
            {
                "gate_id": "stage3c.mrr_relative_gain",
                "approved_on": "2026-07-15",
                "minimum_allowed": 0.0,
                "reason": "固定评估集无 MRR 提升，用户接受受限风险。",
                "evidence": "docs/阶段3执行进度.md",
            }
        ],
    }


def write_policy(
    root: Path,
    data: dict[str, object],
    *,
    create_evidence: bool = True,
) -> Path:
    if create_evidence:
        evidence = root / "docs/阶段3执行进度.md"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text("# 测试证据\n", encoding="utf-8")
    path = root / "policy.json"
    path.write_text(
        json.dumps(data, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_loads_repository_policy() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    policy = load_stage3_quality_policy(
        repo_root / "backend/config/evaluation/stage3-quality-policy.json",
        repo_root=repo_root,
    )
    assert policy.final_mode == "rewrite"
    assert policy.final_thresholds.recall_at_5 == 0.85
    assert policy.waivers[0].gate_id == "stage3c.mrr_relative_gain"


def test_rejects_duplicate_waiver_gate_id(tmp_path: Path) -> None:
    data = valid_policy_dict()
    data["waivers"].append(dict(data["waivers"][0]))
    with pytest.raises(ValidationError, match="重复 waiver gate_id"):
        load_stage3_quality_policy(write_policy(tmp_path, data), repo_root=tmp_path)


def test_rejects_unknown_waiver_gate(tmp_path: Path) -> None:
    data = valid_policy_dict()
    data["waivers"][0]["gate_id"] = "stage3e.recall"
    with pytest.raises(ValidationError, match="不允许豁免"):
        load_stage3_quality_policy(write_policy(tmp_path, data), repo_root=tmp_path)


def test_rejects_missing_waiver_evidence(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="豁免证据文件不存在"):
        load_stage3_quality_policy(
            write_policy(
                tmp_path,
                valid_policy_dict(),
                create_evidence=False,
            ),
            repo_root=tmp_path,
        )
```

另测：比例大于 1、`final_mode=vector`、重复必需分类都失败。

Run:

```powershell
uv run pytest tests/unit/test_evaluation_policy.py -q
```

Expected: import FAIL，指出 `app.evaluation.policy` 不存在。

- [ ] **Step 4：创建版本化质量策略**

`backend/config/evaluation/stage3-quality-policy.json` 内容固定为：

```json
{
  "schema_version": "1.0",
  "final_mode": "rewrite",
  "minimum_case_count": 30,
  "required_categories": [
    "keyword",
    "semantic",
    "refusal",
    "multi_turn",
    "interference"
  ],
  "historical_thresholds": {
    "stage3b_keyword_gain": 0.10,
    "stage3c_mrr_relative_gain": 0.05,
    "stage3d_multi_turn_gain": 0.15
  },
  "final_thresholds": {
    "recall_at_5": 0.85,
    "citation_hit_rate": 0.90,
    "refusal_accuracy": 0.90
  },
  "waivers": [
    {
      "gate_id": "stage3c.mrr_relative_gain",
      "approved_on": "2026-07-15",
      "minimum_allowed": 0.0,
      "reason": "固定 30 条评估集中 hybrid 已有 28 条 RR=1，rerank 的 MRR 相对提升为 0%，用户接受当前无提升风险。",
      "evidence": "docs/阶段3执行进度.md"
    }
  ]
}
```

- [ ] **Step 5：实现严格策略模型**

`backend/app/evaluation/policy.py` 定义：

```python
ALLOWED_WAIVER_GATE_IDS = {"stage3c.mrr_relative_gain"}


class HistoricalThresholds(BaseModel):
    stage3b_keyword_gain: float = Field(ge=0, le=1)
    stage3c_mrr_relative_gain: float = Field(ge=0, le=1)
    stage3d_multi_turn_gain: float = Field(ge=0, le=1)


class FinalThresholds(BaseModel):
    recall_at_5: float = Field(ge=0, le=1)
    citation_hit_rate: float = Field(ge=0, le=1)
    refusal_accuracy: float = Field(ge=0, le=1)


class QualityWaiver(BaseModel):
    gate_id: str
    approved_on: date
    minimum_allowed: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1)
    evidence: Path


class Stage3QualityPolicy(BaseModel):
    schema_version: Literal["1.0"]
    final_mode: Literal["rewrite"]
    minimum_case_count: int = Field(ge=30)
    required_categories: list[EvaluationCategory]
    historical_thresholds: HistoricalThresholds
    final_thresholds: FinalThresholds
    waivers: list[QualityWaiver]

    @model_validator(mode="after")
    def validate_policy(self) -> "Stage3QualityPolicy":
        if len(set(self.required_categories)) != len(self.required_categories):
            raise ValueError("required_categories 不得重复")
        waiver_ids = [waiver.gate_id for waiver in self.waivers]
        if len(set(waiver_ids)) != len(waiver_ids):
            raise ValueError("重复 waiver gate_id")
        if not set(waiver_ids) <= ALLOWED_WAIVER_GATE_IDS:
            raise ValueError("策略包含不允许豁免的 gate")
        return self
```

加载函数使用 `Stage3QualityPolicy.model_validate_json`；每个 `evidence` 必须解析到 `repo_root` 内的现有文件，否则抛 `ValueError("豁免证据文件不存在或不在仓库内")`。

Run: `uv run pytest tests/unit/test_evaluation_policy.py -q`

Expected: PASS。

- [ ] **Step 6：写四报告兼容性失败测试**

`backend/tests/unit/test_evaluation_comparison.py` 在同文件定义以下测试工厂：

- `make_cases()`：按 keyword/semantic/refusal/multi_turn/interference 顺序各生成 6 个 `CaseResult`，共 30 个；默认 recall、MRR、逐案例引用和拒答判断均为 1。
- `make_environment(mode)`：公共字段完全相同；vector 为 vector/disabled，hybrid 为 hybrid/disabled，rerank/rewrite 为 hybrid/local/False，并具有相同的 `BAAI/bge-reranker-base`、cpu、批大小、candidate_k=20 和接受门。
- `make_four_reports()`：生成四份 schema 1.1 报告，共享 dataset SHA、top_k=5、30 个案例、run ID、知识库 UUID、快照、5 个文档和 13 个片段。
- `set_metric(reports, mode, field, value)`：只修改指定报告的总体指标。
- `set_category_recall(reports, mode, category, value)`：修改指定模式和分类的 6 个逐案例 recall。
- `gate(comparison, gate_id)`：返回唯一匹配项；没有或重复时让测试失败。

`policy` fixture 与 `gate` 的实现固定为：

```python
@pytest.fixture
def policy() -> Stage3QualityPolicy:
    repo_root = Path(__file__).resolve().parents[3]
    return load_stage3_quality_policy(
        repo_root / "backend/config/evaluation/stage3-quality-policy.json",
        repo_root=repo_root,
    )


def gate(comparison: Stage3Comparison, gate_id: str) -> GateResult:
    matches = [item for item in comparison.gates if item.gate_id == gate_id]
    assert len(matches) == 1
    return matches[0]
```

每份报告含共享的 30 个案例、schema 1.1 和 provenance。逐项断言以下变化抛出包含字段名的 `ValueError`：

- 缺少、重复或额外模式。
- schema 1.0，错误明确提示重新生成 1.1 报告。
- `dataset_sha256`、`top_k`、`case_count` 不同。
- case ID、分类或顺序不同；单报告内 ID 重复。
- `run_id`、`knowledge_base_id`、`snapshot_sha256`、文档数或片段数不同。
- `generated_at` 只用于追踪，允许四份报告不同，不参与兼容性比较。
- 公共 `embedding_model` 或 `rag_score_threshold` 不同。
- vector/hybrid 开启 Reranker。
- rerank/rewrite 没有 hybrid、local、`fallback=False`，或两者候选数/模型/设备/批大小/接受门不同。

Run:

```powershell
uv run pytest tests/unit/test_evaluation_comparison.py -q
```

Expected: import FAIL，指出 `app.evaluation.comparison` 不存在。

- [ ] **Step 7：实现可比性检查和分类指标**

`backend/app/evaluation/comparison.py` 使用稳定常量：

```python
MODES = ("vector", "hybrid", "rerank", "rewrite")
COMMON_ENV_KEYS = (
    "app_env",
    "embedding_provider",
    "embedding_model",
    "embedding_device",
    "embedding_batch_size",
    "chat_provider",
    "chat_model",
    "embedding_dimensions",
    "rag_score_threshold",
    "rag_rrf_rank_constant",
)
RERANK_ENV_KEYS = (
    "rag_reranker_provider",
    "rag_reranker_model",
    "rag_reranker_device",
    "rag_reranker_batch_size",
    "rag_candidate_k",
    "rag_reranker_allow_fallback",
    "rag_reranker_min_score",
)
```

`_validate_compatibility` 按 Step 6 全部规则执行，错误只含字段、模式和脱敏值。`category_recall` 必须按 `CaseResult.category` 分组并计算 `recall_at_k` 算术平均，不从 ID 推断。

`MetricDelta` 固定字段：`recall_at_5`、`mrr_at_5`、`citation_hit_rate`、`refusal_accuracy`、`latency_p50_ms`、`latency_p95_ms`；每个值是当前模式减 vector。

- [ ] **Step 8：写 gate 与豁免测试**

至少实现以下测试：

```python
def test_final_gate_only_uses_rewrite(policy) -> None:
    reports = make_four_reports()
    set_metric(reports, "vector", "recall_at_5", 0.83)
    set_metric(reports, "rewrite", "recall_at_5", 0.90)
    comparison = compare_stage3_reports(reports, policy)
    assert gate(comparison, "stage3e.recall").status == "passed"


def test_stage3c_zero_gain_is_waived_when_citation_does_not_drop(policy) -> None:
    reports = make_four_reports()
    set_metric(reports, "hybrid", "mrr_at_5", 0.93)
    set_metric(reports, "rerank", "mrr_at_5", 0.93)
    set_metric(reports, "hybrid", "citation_hit_rate", 0.93)
    set_metric(reports, "rerank", "citation_hit_rate", 0.93)
    comparison = compare_stage3_reports(reports, policy)
    result = gate(comparison, "stage3c.mrr_relative_gain")
    assert result.status == "waived"
    assert result.waiver is not None


@pytest.mark.parametrize(
    ("rerank_mrr", "rerank_citation"),
    [(0.92, 0.93), (0.93, 0.92)],
)
def test_stage3c_waiver_does_not_cover_regression(
    policy,
    rerank_mrr: float,
    rerank_citation: float,
) -> None:
    reports = make_four_reports()
    set_metric(reports, "hybrid", "mrr_at_5", 0.93)
    set_metric(reports, "rerank", "mrr_at_5", rerank_mrr)
    set_metric(reports, "hybrid", "citation_hit_rate", 0.93)
    set_metric(reports, "rerank", "citation_hit_rate", rerank_citation)
    comparison = compare_stage3_reports(reports, policy)
    assert comparison.passed is False
```

另测：

- 3B 关键词基线 1.0 时目标仍为 1.0。
- 3D rerank 多轮 0.8333333333333334 时目标为 0.9833333333333334。
- rewrite Recall/引用/拒答任一低于绝对门即失败。
- `failure_case_ids` 只含 `rewrite.recall`、`rewrite.citation`、`rewrite.refusal` 对应的 case ID。

- [ ] **Step 9：实现 12 个稳定 gate**

| gate ID | actual | target |
|---|---:|---:|
| `stage3a.case_count` | case_count | minimum_case_count |
| `stage3a.category_coverage` | 已存在分类数 | 5 |
| `stage3b.overall_recall` | hybrid Recall | vector Recall |
| `stage3b.keyword_recall` | hybrid keyword Recall | `ceiling_aware_target(vector, 0.10)` |
| `stage3b.citation` | hybrid citation | vector citation |
| `stage3b.refusal` | hybrid refusal | vector refusal |
| `stage3c.mrr_relative_gain` | `relative_gain(hybrid, rerank)` | 0.05 |
| `stage3c.citation` | rerank citation | hybrid citation |
| `stage3d.multi_turn_recall` | rewrite multi_turn Recall | `ceiling_aware_target(rerank, 0.15)` |
| `stage3e.recall` | rewrite Recall | 0.85 |
| `stage3e.citation` | rewrite citation | 0.90 |
| `stage3e.refusal` | rewrite refusal | 0.90 |

结构化结果固定为：

```python
class MetricDelta(BaseModel):
    recall_at_5: float
    mrr_at_5: float
    citation_hit_rate: float
    refusal_accuracy: float
    latency_p50_ms: float
    latency_p95_ms: float


class GateResult(BaseModel):
    gate_id: str
    status: GateStatus
    actual: float | int | str
    target: float | int | str
    message: str
    waiver: QualityWaiver | None = None


class Stage3Comparison(BaseModel):
    reports: dict[EvaluationMode, EvaluationReport]
    metric_deltas: dict[EvaluationMode, MetricDelta]
    category_recall: dict[
        EvaluationMode,
        dict[EvaluationCategory, float],
    ]
    gates: list[GateResult]
    failure_case_ids: dict[str, list[str]]
    recommended_mode: Literal["rewrite"] = "rewrite"
    fallback_mode: Literal["vector"] = "vector"
    passed: bool
    sanitized_failures: list[str]
```

3C MRR 达标为 `passed`；未达 5% 但实际不小于 0、rerank MRR 不低于 hybrid、引用不低于 hybrid 且策略豁免有效时为 `waived`；其余为 `failed`。`comparison.passed` 只在所有 gate 均为 passed 或 waived 时为真。

- [ ] **Step 10：验证、更新看板并提交**

Run:

```powershell
uv run pytest tests/unit/test_evaluation_metrics.py tests/unit/test_evaluation_policy.py tests/unit/test_evaluation_comparison.py -q
uv run ruff check app tests migrations scripts
uv run ruff format --check app tests migrations scripts
Set-Location ..
git diff --check
```

Expected: PASS。

看板更新为总进度 `23 / 26`、3E `2 / 5`，勾选 Task 2，记录 12 个 gate 和 3C 受限豁免证据，下一步为 Task 3/`Terra｜medium`。勾选本 Task 全部步骤。

Commit:

```powershell
git add backend/app/evaluation/metrics.py backend/app/evaluation/policy.py backend/app/evaluation/comparison.py backend/config/evaluation/stage3-quality-policy.json backend/tests/unit/test_evaluation_metrics.py backend/tests/unit/test_evaluation_policy.py backend/tests/unit/test_evaluation_comparison.py docs/阶段3执行进度.md docs/superpowers/plans/2026-07-15-stage-3e-quality-acceptance.md
git commit -m "feat: 自动比较阶段3质量门"
```

---

### Task 3：脱敏中文验收报告

**模型：** `Terra｜medium`

**Files:**

- Create: `backend/app/evaluation/reporting.py`
- Create: `backend/tests/unit/test_evaluation_reporting.py`
- Modify: `docs/阶段3执行进度.md`
- Modify: `docs/superpowers/plans/2026-07-15-stage-3e-quality-acceptance.md`

**Interfaces:**

- Consumes: `Stage3Comparison`、稳定 gate ID、四份报告的 provenance。
- Produces: `ensure_public_report_safe(markdown)` 和 `render_stage3_markdown(comparison, *, reproduce_command)`。

- [ ] **Step 1：写固定章节与顺序失败测试**

`backend/tests/unit/test_evaluation_reporting.py` 定义固定章节：

同文件定义 `make_render_comparison(*, passed, stage3c_status, failure_case_ids)`：直接构造 Task 2 定义的 `Stage3Comparison`；四份报告使用同一 30 案例、run ID、知识库和快照，默认 rewrite 指标为 0.9666666667，3C gate 的实际值为 0、目标为 0.05。三个 pytest fixture 固定为：

- `passing_comparison`：全部 gate passed，`passed=True`，失败 ID 为空。
- `waived_comparison`：只有 `stage3c.mrr_relative_gain` 为 waived 并带策略豁免，其余 passed，整体 `passed=True`。
- `failing_comparison`：`stage3e.recall` failed，`rewrite.recall=["multi-turn-06"]`，整体 `passed=False`。

测试文件不导入其他测试模块，也不调用数据库、Provider 或 `scripts.accept_stage3`。

```python
HEADINGS = [
    "## 1. 验收结论",
    "## 2. 数据集与知识库快照",
    "## 3. 执行环境",
    "## 4. 四模式总体指标",
    "## 5. 各分类 Recall@5",
    "## 6. 相对 vector 的提升或退化",
    "## 7. 3A～3E 质量门结果",
    "## 8. 3C 风险豁免及适用边界",
    "## 9. 失败案例 ID",
    "## 10. 最终推荐配置与纯向量回退配置",
    "## 11. 可重复执行命令",
    "## 12. 已知风险与延迟说明",
]


def test_report_has_fixed_heading_and_mode_order(passing_comparison) -> None:
    markdown = render_stage3_markdown(
        passing_comparison,
        reproduce_command="uv run python -m scripts.accept_stage3 --dataset stage3.jsonl",
    )
    positions = [markdown.index(heading) for heading in HEADINGS]
    assert positions == sorted(positions)
    mode_positions = [
        markdown.index(f"| {mode} |")
        for mode in ("vector", "hybrid", "rerank", "rewrite")
    ]
    assert mode_positions == sorted(mode_positions)
```

Run:

```powershell
Set-Location backend
uv run pytest tests/unit/test_evaluation_reporting.py -q
```

Expected: import FAIL，指出 `app.evaluation.reporting` 不存在。

- [ ] **Step 2：写格式、豁免和隐私失败测试**

测试必须包含：

```python
def test_report_formats_metrics_and_waiver(waived_comparison) -> None:
    markdown = render_stage3_markdown(
        waived_comparison,
        reproduce_command="accept-stage3",
    )
    assert "96.67%" in markdown
    assert "质量门未通过、已获风险豁免" in markdown
    assert "至少 5.00%" in markdown
    assert "实际 0.00%" in markdown
    assert "MRR 负增长或引用下降时豁免不适用" in markdown


def test_report_only_exposes_failure_case_ids(failing_comparison) -> None:
    markdown = render_stage3_markdown(
        failing_comparison,
        reproduce_command="accept-stage3",
    )
    assert "multi-turn-06" in markdown
    assert "这个问题的全文不能公开" not in markdown
    assert "片段原文不能公开" not in markdown
    knowledge_base_id = failing_comparison.reports["rewrite"].provenance.knowledge_base_id
    assert str(knowledge_base_id) not in markdown


@pytest.mark.parametrize(
    "secret",
    [
        "database_url=private",
        "api_key=private",
        "access_token=private",
        "postgresql+psycopg://user:pass@localhost/db",
    ],
)
def test_sensitive_marker_scan_rejects_secret(secret: str) -> None:
    with pytest.raises(ValueError, match="公开报告包含敏感标记"):
        ensure_public_report_safe(secret)
```

run ID 可以完整显示；知识库 UUID 不显示；快照只显示前 12 位。

- [ ] **Step 3：实现纯函数 Markdown 渲染器**

`backend/app/evaluation/reporting.py` 固定辅助函数：

```python
SENSITIVE_MARKERS = (
    "database_url",
    "api_key",
    "access_token",
    "postgresql://",
    "postgresql+psycopg://",
)


def _percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def _milliseconds(value: float) -> str:
    return f"{value:.2f} ms"


def ensure_public_report_safe(markdown: str) -> None:
    lowered = markdown.casefold()
    if any(marker in lowered for marker in SENSITIVE_MARKERS):
        raise ValueError("公开报告包含敏感标记")
```

`render_stage3_markdown` 按以下确定性规则拼接：

1. 标题固定为 `# 阶段 3 RAG 质量综合验收报告`，结尾恰好一个换行。
2. 按 Step 1 的 12 节顺序输出。
3. 结论写“通过”或“未通过”和 rewrite 三项最终指标。
4. 数据节只写 dataset SHA、run ID、快照前 12 位、文档数、片段数、Top K 和案例数。
5. 环境节只渲染 `build_safe_environment` 的公开字段，不输出未知键。
6. 总体表按 vector/hybrid/rerank/rewrite 顺序展示 Recall、MRR、引用、拒答、P50、P95。
7. 分类表按 keyword/semantic/refusal/multi_turn/interference 顺序。
8. gate 表按 Task 2 的 12 个 gate 顺序，状态中文映射为 `通过/未通过/已豁免`。
9. 3C 节同时展示原门槛、实际值、原因、证据路径和严格边界。
10. 失败案例只读取 `failure_case_ids`；没有失败写“无”。
11. 推荐固定 rewrite；回退固定 vector，并说明回退减少本地重排和改写依赖。
12. 重现命令放入 PowerShell code fence；调用方提供文字，不展开环境变量。
13. 已知风险说明 MRR、P50、P95 不是 3E 绝对门，真实模型延迟会波动。
14. 3D 选择性改写与 `QUESTION_REWRITE_ERROR` 回退只引用自动化测试证据，不从 JSON 指标虚构结论。
15. 返回前调用 `ensure_public_report_safe`。

- [ ] **Step 4：验证、更新看板并提交**

Run:

```powershell
uv run pytest tests/unit/test_evaluation_reporting.py -q
uv run ruff check app tests migrations scripts
uv run ruff format --check app tests migrations scripts
Set-Location ..
git diff --check
```

Expected: PASS。

看板更新为总进度 `24 / 26`、3E `3 / 5`，勾选 Task 3，记录 12 节和敏感扫描证据，下一步为 Task 4/`Sol｜xhigh`。勾选本 Task 全部步骤。

Commit:

```powershell
git add backend/app/evaluation/reporting.py backend/tests/unit/test_evaluation_reporting.py docs/阶段3执行进度.md docs/superpowers/plans/2026-07-15-stage-3e-quality-acceptance.md
git commit -m "feat: 生成阶段3脱敏验收报告"
```

---

### Task 4：`accept_stage3` 验收编排器

**模型：** `Sol｜xhigh`

**Files:**

- Create: `backend/scripts/accept_stage3.py`
- Create: `backend/tests/unit/test_accept_stage3_script.py`
- Modify: `backend/app/evaluation/schemas.py`
- Modify: `backend/tests/unit/test_evaluate_rag_script.py`
- Modify: `docs/阶段3执行进度.md`
- Modify: `docs/superpowers/plans/2026-07-15-stage-3e-quality-acceptance.md`

**Interfaces:**

- Consumes: `run_from_args`、`compute_knowledge_base_snapshot`、`load_stage3_quality_policy`、`compare_stage3_reports`、`render_stage3_markdown`。
- Produces: `Stage3AcceptanceManifest`、`AcceptanceRun`、`parse_args`、`run_acceptance`、`write_acceptance_bundle` 和 `main`。

`backend/tests/unit/test_accept_stage3_script.py` 的本地辅助对象必须在文件顶部定义：

- `valid_arguments()` 返回 Step 1 的完整参数列表。
- `args` fixture 在 `tmp_path` 写入有效 dataset、策略和证据文件，再调用 `parse_args(valid_arguments())`。
- `make_report(mode, run_id, snapshot)` 返回具有 30 案例、固定安全环境和匹配 provenance 的 1.1 报告。
- `passing_run()` 与 `failing_run()` 返回完整 `AcceptanceRun`；两者只在 `comparison.passed`、失败 gate 和 `manifest.passed` 上不同。
- `write_bundle_fixture(tmp_path)` 调用真实 `write_acceptance_bundle`，把返回值包装为具有 `manifest_path/report_paths/markdown_path` 的 `AcceptanceRun`。
- `assert_all_artifact_hashes_match(manifest, run)` 对五个文件逐字节计算 SHA-256 并与 manifest 比较。

这些辅助对象只创建 `tmp_path` 文件和 Pydantic 模型，不访问真实数据库或模型。

- [ ] **Step 1：写 CLI 参数与旧 CLI 兼容测试**

`backend/tests/unit/test_accept_stage3_script.py`：

```python
def test_parse_args_uses_fixed_stage3_outputs(tmp_path: Path) -> None:
    knowledge_base_id = uuid4()
    args = parse_args(
        [
            "--dataset",
            str(tmp_path / "stage3.jsonl"),
            "--knowledge-base-id",
            str(knowledge_base_id),
            "--policy",
            str(tmp_path / "policy.json"),
            "--reports-dir",
            str(tmp_path / "reports"),
            "--markdown-output",
            str(tmp_path / "阶段3质量验收报告.md"),
        ]
    )
    assert args.knowledge_base_id == knowledge_base_id
    assert args.top_k == 5
    assert args.reports_dir.name == "reports"


def test_existing_single_mode_cli_remains_flat() -> None:
    args = evaluate_rag.parse_args(
        [
            "--dataset",
            "stage3.jsonl",
            "--knowledge-base-id",
            str(uuid4()),
            "--mode",
            "rewrite",
            "--output",
            "rewrite.json",
        ]
    )
    assert args.mode == "rewrite"
```

新参数固定为 `--dataset`、`--knowledge-base-id`、`--policy`、`--reports-dir`、`--markdown-output` 和可选 `--top-k`；不得提供 CLI 阈值覆盖参数。

Run:

```powershell
Set-Location backend
uv run pytest tests/unit/test_accept_stage3_script.py -q
```

Expected: import FAIL，指出 `scripts.accept_stage3` 不存在。

- [ ] **Step 2：写顺序运行和共享溯源失败测试**

用 `AsyncMock` 注入基准快照和单模式执行器：

```python
@pytest.mark.asyncio
async def test_runs_four_modes_in_order_with_shared_snapshot_and_run_id(
    monkeypatch: pytest.MonkeyPatch,
    args: argparse.Namespace,
) -> None:
    snapshot = KnowledgeBaseSnapshot(args.knowledge_base_id, "a" * 64, 5, 13)
    calls: list[tuple[str, UUID, KnowledgeBaseSnapshot]] = []

    async def fake_run(mode_args, settings, *, run_id, expected_snapshot):
        calls.append((mode_args.mode, run_id, expected_snapshot))
        return make_report(mode_args.mode, run_id=run_id, snapshot=snapshot)

    monkeypatch.setattr("scripts.accept_stage3.run_from_args", fake_run)
    monkeypatch.setattr(
        "scripts.accept_stage3.compute_baseline_snapshot",
        AsyncMock(return_value=snapshot),
    )
    result = await run_acceptance(args, Settings(_env_file=None))
    assert [call[0] for call in calls] == [
        "vector",
        "hybrid",
        "rerank",
        "rewrite",
    ]
    assert len({call[1] for call in calls}) == 1
    assert all(call[2] == snapshot for call in calls)
    assert result.comparison.reports["rewrite"].provenance.run_id == calls[0][1]
```

`compute_baseline_snapshot` 只使用 `session_factory` 和 Task 1 的快照函数读取 S0；每个 `run_from_args` 自己再次执行模式前后检查。

- [ ] **Step 3：写退出码语义测试**

`main` 的测试固定为：

```python
def test_main_returns_zero_on_success(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.accept_stage3.run_acceptance_command",
        lambda args, settings: passing_run(),
    )
    assert main(valid_arguments()) == 0


def test_main_returns_two_on_quality_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.accept_stage3.run_acceptance_command",
        lambda args, settings: failing_run(),
    )
    assert main(valid_arguments()) == 2


def test_main_returns_one_on_input_error_without_secret(
    monkeypatch,
    capsys,
) -> None:
    secret = "postgresql+psycopg://private:private@localhost/private"
    monkeypatch.setattr(
        "scripts.accept_stage3.run_acceptance_command",
        Mock(side_effect=RuntimeError(secret)),
    )
    assert main(valid_arguments()) == 1
    assert secret not in capsys.readouterr().out
```

控制台错误只使用异常类别或 `sanitized_failures`，不能拼接底层异常消息。

- [ ] **Step 4：写原子输出与 manifest 失败测试**

测试：

```python
def test_writes_manifest_last_and_hashes_all_artifacts(
    tmp_path,
    monkeypatch,
) -> None:
    replaced: list[str] = []
    real_replace = os.replace

    def recording_replace(source, target):
        replaced.append(Path(target).name)
        real_replace(source, target)

    monkeypatch.setattr("scripts.accept_stage3.os.replace", recording_replace)
    run = write_bundle_fixture(tmp_path)
    assert replaced[-1] == "stage3e-manifest.json"
    manifest = Stage3AcceptanceManifest.model_validate_json(
        run.manifest_path.read_text(encoding="utf-8")
    )
    assert set(manifest.artifacts) == {
        "stage3e-vector.json",
        "stage3e-hybrid.json",
        "stage3e-rerank.json",
        "stage3e-rewrite.json",
        "docs/阶段3质量验收报告.md",
    }
    assert_all_artifact_hashes_match(manifest, run)


def test_replace_failure_does_not_update_manifest(tmp_path, monkeypatch) -> None:
    old_manifest = tmp_path / "reports/stage3e-manifest.json"
    old_manifest.parent.mkdir()
    old_manifest.write_text('{"old": true}', encoding="utf-8")
    monkeypatch.setattr(
        "scripts.accept_stage3.os.replace",
        Mock(side_effect=[None, OSError("disk full")]),
    )
    with pytest.raises(OSError):
        write_bundle_fixture(tmp_path)
    assert old_manifest.read_text(encoding="utf-8") == '{"old": true}'
```

另测：输入/兼容性错误不调用写入；质量失败仍写五个产物；临时目录退出后消失；`user-report.json` 不被删除。

- [ ] **Step 5：增加 manifest 模型**

`backend/app/evaluation/schemas.py` 复用 Task 1 的 `GateStatus`，增加：

```python
class Stage3AcceptanceManifest(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: UUID
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifacts: dict[str, str]
    gate_statuses: dict[str, GateStatus]
    passed: bool

    @field_validator("artifacts")
    @classmethod
    def validate_artifact_hashes(cls, value: dict[str, str]) -> dict[str, str]:
        valid_digits = set("0123456789abcdef")
        if len(value) != 5:
            raise ValueError("manifest 必须包含五个产物")
        if any(len(digest) != 64 or set(digest) - valid_digits for digest in value.values()):
            raise ValueError("manifest 包含无效 SHA-256")
        return value
```

`backend/scripts/accept_stage3.py` 定义返回对象：

```python
@dataclass(frozen=True)
class AcceptanceRun:
    comparison: Stage3Comparison
    manifest: Stage3AcceptanceManifest
    report_paths: dict[EvaluationMode, Path]
    markdown_path: Path
    manifest_path: Path
```

- [ ] **Step 6：实现编排顺序和固定路径**

`backend/scripts/accept_stage3.py` 固定：

```python
MODES = ("vector", "hybrid", "rerank", "rewrite")
REPORT_NAMES = {
    "vector": "stage3e-vector.json",
    "hybrid": "stage3e-hybrid.json",
    "rerank": "stage3e-rerank.json",
    "rewrite": "stage3e-rewrite.json",
}
MANIFEST_NAME = "stage3e-manifest.json"
```

`run_acceptance` 顺序：

1. 加载策略并校验证据。
2. 校验 dataset、policy、输出路径，拒绝 Markdown 与任一 JSON 同路径。
3. 生成一次 `run_id`，计算一次 S0。
4. 按 MODES 顺序构造现有单模式 Namespace。
5. 每次调用 `await run_from_args(mode_args, settings, run_id=run_id, expected_snapshot=S0)`。
6. 四份报告齐备后比较；兼容性错误发生在正式输出前。
7. 渲染不含知识库 UUID 的重现命令和 Markdown。
8. 原子写入，返回 `AcceptanceRun`；质量失败不抛异常。

`run_acceptance_command` 必须使用：

```python
def run_acceptance_command(
    args: argparse.Namespace,
    settings: Settings,
) -> AcceptanceRun:
    with asyncio.Runner(loop_factory=new_event_loop) as runner:
        return runner.run(run_acceptance(args, settings))
```

- [ ] **Step 7：实现安全原子写入**

`write_acceptance_bundle`：

1. 调用 `ensure_public_report_safe` 检查 Markdown；每份序列化 JSON 也用同一敏感标记集合扫描。
2. 用 `TemporaryDirectory(prefix=".stage3e-", dir=reports_dir)`。
3. 四份 JSON 使用 `model_dump(mode="json")`、`ensure_ascii=False`、2 空格缩进和末尾换行。
4. Markdown 写入同一临时目录。
5. 对四份 JSON 和 Markdown 原始 bytes 计算 SHA-256。
6. 构造 `Stage3AcceptanceManifest`，记录 run ID、快照、五个哈希、12 个 gate 状态和 passed。
7. 依次 `os.replace` 四份 JSON，再替换 Markdown，最后替换 manifest。
8. 返回 manifest、四报告路径、Markdown 路径和 manifest 路径。

若中途替换失败，旧 manifest 必须保持不变；消费者用 manifest 判断这组文件是否完整。

- [ ] **Step 8：实现退出码和最小摘要**

`main` 必须：

- 成功执行后输出 run ID、四个报告路径、Markdown 路径、rewrite 三项指标和 gate 状态。
- `comparison.passed=True` 返回 0。
- `comparison.passed=False` 输出脱敏失败原因并返回 2。
- 参数、文件、schema、策略、快照、环境或输出异常返回 1。
- 模块结尾使用 `raise SystemExit(main())`。

`format_acceptance_error` 对 `FileNotFoundError`、`ValueError`、`ValidationError`、`OSError` 返回固定中文类别，不拼接原始异常文本。

- [ ] **Step 9：运行 Task 4 聚焦回归**

Run:

```powershell
uv run pytest tests/unit/test_accept_stage3_script.py tests/unit/test_evaluate_rag_script.py tests/unit/test_evaluation_schemas.py tests/unit/test_evaluation_snapshot.py tests/unit/test_evaluation_policy.py tests/unit/test_evaluation_comparison.py tests/unit/test_evaluation_reporting.py -q
uv run python -m scripts.accept_stage3 --help
uv run python -m scripts.evaluate_rag --help
uv run ruff check app tests migrations scripts
uv run ruff format --check app tests migrations scripts
Set-Location ..
git diff --check
```

Expected: 测试 PASS；两个帮助命令返回 0；新 CLI 无门槛覆盖参数；旧 CLI 仍有四模式；Ruff 和 diff 通过。

- [ ] **Step 10：更新看板并提交**

看板更新为总进度 `25 / 26`、3E `4 / 5`，勾选 Task 4，记录 0/1/2、顺序运行、manifest 最后写和旧 CLI 兼容证据，下一步为 Task 5/`Sol｜xhigh`。勾选本 Task 全部步骤。

Commit:

```powershell
git add backend/app/evaluation/schemas.py backend/scripts/accept_stage3.py backend/tests/unit/test_accept_stage3_script.py backend/tests/unit/test_evaluate_rag_script.py docs/阶段3执行进度.md docs/superpowers/plans/2026-07-15-stage-3e-quality-acceptance.md
git commit -m "feat: 编排第三阶段一键质量验收"
```

---

### Task 5：真实验收、演示与第三阶段收口

**模型：** `Sol｜xhigh`

**Files:**

- Create/Generate: `docs/阶段3质量验收报告.md`
- Create: `docs/阶段3验证与演示.md`
- Modify: `README.md`
- Modify: `docs/学习笔记.md`
- Modify: `docs/阶段3执行进度.md`
- Modify: `docs/superpowers/plans/2026-07-15-stage-3e-quality-acceptance.md`
- Verify only: `backend/reports/stage3e-vector.json`
- Verify only: `backend/reports/stage3e-hybrid.json`
- Verify only: `backend/reports/stage3e-rerank.json`
- Verify only: `backend/reports/stage3e-rewrite.json`
- Verify only: `backend/reports/stage3e-manifest.json`

**Interfaces:**

- Consumes: Task 1～4 的完整 CLI、真实知识库 `f1279eb3-dcfd-490e-ad33-973e23df5e5e`、固定 30 条 `stage3.jsonl`。
- Produces: 真实四模式证据、正式脱敏报告、五分钟演示、第三阶段最终状态和最后提交。

- [ ] **Step 1：执行前安全检查**

Run:

```powershell
Set-Location D:\学习\AI-Knowledge-Assistant\.worktrees\stage-3e-quality-acceptance
git status --short
git branch --show-current
git log -5 --oneline
git diff --check
Test-Path backend\tests\fixtures\evaluation\stage3.jsonl
Test-Path backend\config\evaluation\stage3-quality-policy.json
Test-Path D:\学习\AI-Knowledge-Assistant\.worktrees\stage-2d-question-experience\backend\.env
```

Expected: 分支正确，工作树仅有预期 Task 5 文档状态或为空，三个 `Test-Path` 都是 True。不读取、不打印 `.env` 内容。

- [ ] **Step 2：运行真实四模式一键验收**

在 `backend` 目录执行；`uv --env-file` 只把既有环境加载到当前子进程，不复制配置文件：

```powershell
Set-Location backend
$sourceEnv = "D:\学习\AI-Knowledge-Assistant\.worktrees\stage-2d-question-experience\backend\.env"
uv run --env-file $sourceEnv python -m scripts.accept_stage3 `
  --dataset tests/fixtures/evaluation/stage3.jsonl `
  --knowledge-base-id f1279eb3-dcfd-490e-ad33-973e23df5e5e `
  --policy config/evaluation/stage3-quality-policy.json `
  --reports-dir reports `
  --markdown-output ..\docs\阶段3质量验收报告.md
$acceptanceExitCode = $LASTEXITCODE
Write-Output "accept_stage3 exit code: $acceptanceExitCode"
```

Expected:

- 门禁成功返回 0，四份报告、Markdown 和 manifest 都生成。
- 控制台只输出 run ID、路径、rewrite 三项指标、门禁和脱敏失败原因。
- 返回 1 时按输入/环境/快照错误诊断，修复后从聚焦测试重跑。
- 返回 2 时保留真实失败报告，3E 保持进行中；不得改数据集、策略阈值或 Markdown 制造通过。

- [ ] **Step 3：独立校验 manifest、同源性和最终门**

用内联 Python 只输出非敏感摘要：

```powershell
@'
import hashlib
import json
from pathlib import Path

reports_dir = Path("reports")
manifest = json.loads(
    (reports_dir / "stage3e-manifest.json").read_text(encoding="utf-8")
)
modes = ("vector", "hybrid", "rerank", "rewrite")
reports = {
    mode: json.loads(
        (reports_dir / f"stage3e-{mode}.json").read_text(encoding="utf-8")
    )
    for mode in modes
}
assert {report["schema_version"] for report in reports.values()} == {"1.1"}
assert len({report["provenance"]["run_id"] for report in reports.values()}) == 1
assert len(
    {report["provenance"]["snapshot_sha256"] for report in reports.values()}
) == 1
assert len({report["dataset_sha256"] for report in reports.values()}) == 1
assert len({report["top_k"] for report in reports.values()}) == 1
assert len(
    {
        tuple(case["case_id"] for case in report["cases"])
        for report in reports.values()
    }
) == 1
rewrite = reports["rewrite"]
assert rewrite["recall_at_5"] >= 0.85
assert rewrite["citation_hit_rate"] >= 0.90
assert rewrite["refusal_accuracy"] >= 0.90
for name, expected in manifest["artifacts"].items():
    path = Path("..") / name if name.startswith("docs/") else reports_dir / name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, name
assert manifest["gate_statuses"]["stage3c.mrr_relative_gain"] in {
    "passed",
    "waived",
}
assert manifest["passed"] is True
print(
    {
        "run_id": manifest["run_id"],
        "snapshot": manifest["snapshot_sha256"][:12],
        "rewrite_recall_at_5": rewrite["recall_at_5"],
        "rewrite_citation_hit_rate": rewrite["citation_hit_rate"],
        "rewrite_refusal_accuracy": rewrite["refusal_accuracy"],
        "stage3c": manifest["gate_statuses"]["stage3c.mrr_relative_gain"],
        "passed": manifest["passed"],
    }
)
'@ | uv run python -
```

Expected: 所有断言通过，只输出 run ID、12 位快照、rewrite 三项指标、3C 状态和 passed。

- [ ] **Step 4：运行后端全量、Ruff 和格式检查**

Run:

```powershell
uv run pytest -q
uv run ruff check app tests migrations scripts
uv run ruff format --check app tests migrations scripts
```

Expected: 全量 pytest、Ruff check 和 format check 通过；记录本轮真实 passed、skipped 和文件数，不沿用 3D 数字。

- [ ] **Step 5：临时空库验证从零迁移、全部 integration、降级和重升级**

从仓库根目录执行，`finally` 必须运行：

```powershell
Set-Location (git rev-parse --show-toplevel)
docker compose -f deploy/docker-compose.yml up -d
$testDatabase = "knowledge_stage3e_$([Guid]::NewGuid().ToString('N').Substring(0, 8))"
try {
  docker compose -f deploy/docker-compose.yml exec -T postgres createdb -U knowledge $testDatabase
  $env:DATABASE_URL = "postgresql+psycopg://knowledge:knowledge@localhost:5432/$testDatabase"
  $env:RUN_DATABASE_TESTS = "1"
  Set-Location backend
  uv run alembic upgrade head
  uv run pytest tests/integration -q
  uv run alembic downgrade 20260714_04
  uv run alembic upgrade head
}
finally {
  Remove-Item Env:RUN_DATABASE_TESTS -ErrorAction SilentlyContinue
  Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
  Set-Location (git rev-parse --show-toplevel)
  docker compose -f deploy/docker-compose.yml exec -T postgres `
    dropdb --if-exists --force -U knowledge $testDatabase
  $remaining = docker compose -f deploy/docker-compose.yml exec -T postgres `
    psql -U knowledge -d knowledge -tAc `
    "SELECT count(*) FROM pg_database WHERE datname = '$testDatabase'"
  Write-Output "temporary database remaining: $($remaining.Trim())"
}
```

Expected: 空库升级到 head，全部 integration PASS，可降级到 `20260714_04` 并重新升级，最后输出 `temporary database remaining: 0`。

- [ ] **Step 6：运行前端全量测试与生产构建**

Run:

```powershell
Set-Location frontend
npm.cmd test -- --run
npm.cmd run build
```

Expected: 全量 Vitest PASS，Vue TypeScript 检查和 Vite build 成功；记录真实测试文件数和测试数。

- [ ] **Step 7：正式报告隐私与一致性复核**

Run:

```powershell
Set-Location ..
rg -n -i "database_url|api_key|access_token|postgresql://|postgresql\+psycopg://" docs/阶段3质量验收报告.md
rg -n "^## (1|2|3|4|5|6|7|8|9|10|11|12)\." docs/阶段3质量验收报告.md
rg -n "质量门未通过、已获风险豁免|MRR 负增长或引用下降时豁免不适用|rewrite|vector" docs/阶段3质量验收报告.md
```

Expected:

- 第一条无匹配，退出码 1 表示未发现敏感标记。
- 12 个章节按顺序各出现一次。
- 3C 豁免、严格边界、推荐 rewrite 和回退 vector 都存在。
- 正式报告不得手工改写机器生成指标；修正时先改渲染器测试，再重跑一键验收。

- [ ] **Step 8：编写五分钟演示文档**

创建 `docs/阶段3验证与演示.md`，固定结构：

1. 前置条件：Docker PostgreSQL、后端环境、真实模型、本地报告忽略规则。
2. 一键命令：完整 `scripts.accept_stage3` 命令，不写秘密。
3. 产物说明：四 JSON、manifest、正式 Markdown。
4. 五分钟时间线：
   - 0:00～0:40：阶段 3 问题和 30 条评估集。
   - 0:40～1:30：vector 到 hybrid 的关键词召回演进。
   - 1:30～2:15：rerank 及 3C 未通过但受限豁免。
   - 2:15～3:10：rewrite 的多轮改写、原问题 Prompt 和安全回退。
   - 3:10～4:10：同快照四模式表、最终三项绝对门。
   - 4:10～5:00：manifest、防泄漏、vector 回退。
5. 现场问题：编号关键词、语义、多轮指代、无答案拒答各一条；不复制私密片段。
6. 失败演示：解释退出码 1/2，不破坏数据库或策略。
7. 回退：使用既有 `RAG_RETRIEVAL_MODE=vector`，Reranker disabled，不改数据库 schema。

- [ ] **Step 9：更新 README 和学习笔记**

`README.md` 增加“阶段 3 RAG 质量验收”：

- 链接正式报告和演示文档。
- 给出一键命令与 0/1/2 含义。
- 明确 JSON/manifest 在本地、Markdown 可提交。
- 明确最终推荐 rewrite、回退 vector、3C 原门未通过但受限豁免。

`docs/学习笔记.md` 增加“从 C# 测试流水线理解 RAG 质量门”：

- 四模式报告类比同一输入快照上的四个实现版本。
- `EvaluationProvenance` 类比 DTO 审计字段。
- manifest 类比发布包校验清单。
- 3C waiver 类比受条件限制的风险签字，不等于测试通过。
- 解释最终绝对门只验收 rewrite，以及保留 vector 回退的原因。

- [ ] **Step 10：完成前总审查**

Run:

```powershell
Set-Location (git rev-parse --show-toplevel)
git status --short
git diff --stat
git diff --check
git diff -- README.md docs/学习笔记.md docs/阶段3执行进度.md docs/阶段3验证与演示.md docs/阶段3质量验收报告.md docs/superpowers/plans/2026-07-15-stage-3e-quality-acceptance.md
git ls-files backend/reports
```

审查：

- 只改 3E 范围文件。
- `git ls-files backend/reports` 不列出原始 JSON/manifest。
- 3C 始终写“质量门未通过、已获风险豁免”。
- 3E 最终门只看 rewrite。
- 真实报告和文档数字一致。
- 没有秘密、问题全文、片段全文或完整 Prompt。
- HTTP/SSE、前端交互、数据库 schema 和固定数据集没有无关修改。
- 任一 gate 或自动验证失败时停止收口，保持进行中并记录失败。

- [ ] **Step 11：更新最终进度与计划**

只有 Steps 2～10 全部通过时：

- `docs/阶段3执行进度.md`：Task 5 勾选，总进度 `26 / 26`，3E `5 / 5`，3E 和第三阶段都为 `已完成`。
- 当前阻塞写“无”；3C 风险作为已知豁免保留。
- 最近验证写本轮真实后端、integration、前端、Ruff、迁移和临时库清理数字。
- 验证证据新增 3E Task 1～5；Task 5 写真实 run ID、快照前 12 位和最终指标。
- “继续执行时的最小提示”改为“第三阶段已完成；下一阶段必须另行设计和确认”，不得标记下一阶段开始。
- 勾选本计划 Task 5 全部步骤，并记录真实验收摘要。

- [ ] **Step 12：提交第三阶段收口**

Run:

```powershell
git add README.md docs/学习笔记.md docs/阶段3执行进度.md docs/阶段3验证与演示.md docs/阶段3质量验收报告.md docs/superpowers/plans/2026-07-15-stage-3e-quality-acceptance.md
git diff --cached --check
git diff --cached --stat
git commit -m "docs: 完成第三阶段检索质量验收"
git status --short
git log -5 --oneline
```

Expected: staged diff 通过，提交成功，工作树干净，最近 5 个提交包含 3E 的五个独立 Task 提交。

---

## 最终完成定义

- Task 1～5 各有红灯、最小实现、绿灯、Ruff/diff 和独立提交证据。
- 四报告均为 schema 1.1，具有相同 run ID、知识库快照、数据集、Top K、案例 ID/分类/顺序和公共环境。
- 每种模式运行前后快照都与 S0 一致。
- 12 个 gate 从真实报告重算；3C 为 passed 或严格范围内 waived，没有未豁免失败。
- rewrite Recall@5 ≥ 0.85、引用命中率 ≥ 0.90、拒答准确率 ≥ 0.90。
- manifest 最后写入，五个产物哈希全部匹配。
- 正式中文报告 12 节完整、指标一致、敏感扫描通过。
- 后端全量、全部 integration、Ruff、format、迁移降级/升级、前端测试/build、`git diff --check` 全部通过。
- 临时数据库由 `finally` 删除并确认残留 0。
- README、学习笔记、执行进度、验收报告和演示文档结论一致。
- 原始 JSON/manifest 仍被 Git 忽略，正式 Markdown 已提交。
- 3C 失败事实和受限豁免永久保留；下一阶段没有被提前开发或标记开始。

## 计划自检清单

- [x] 规格覆盖：设计文档第 1～18 节均映射到 Task 1～5 的具体步骤。
- [x] 占位扫描：正文不存在未定实现、模糊错误处理或无测试细节的代码步骤。
- [x] 类型一致：`KnowledgeBaseSnapshot`、`EvaluationProvenance`、`Stage3QualityPolicy`、`Stage3Comparison`、`Stage3AcceptanceManifest` 和 `AcceptanceRun` 在生产代码与测试中名称一致。
- [x] Gate 一致：策略、比较器、测试、Markdown 和 manifest 使用同一组 12 个 gate ID。
- [x] CLI 一致：旧 `evaluate_rag` 保持平铺单模式参数，新 `accept_stage3` 独立编排。
- [x] 安全一致：错误、控制台、Markdown 和输出扫描都不暴露秘密或检索原文。
- [x] 状态一致：执行时总数依次为 21/26 → 22/26 → 23/26 → 24/26 → 25/26 → 26/26。

# 阶段 3E：RAG 质量综合评估与验收实施计划

> **执行要求：** 使用 `executing-plans` 逐 Task 执行；除非用户明确要求，不派发子代理。所有步骤用本文复选框跟踪。

**目标：** 使用同一数据集比较四种检索链路，自动执行质量门并形成可复现的中文验收报告。

**架构：** 评估脚本输出稳定 JSON；比较器读取多个同版本报告，计算差异并检查阈值；Markdown 渲染器只消费结构化结果，不重新调用模型。

**技术栈：** Python 3.12、Pydantic、pytest、JSON、Markdown。

## 全局约束

- 3A、3B、3C、3D 必须全部完成；3D 阻塞时第三阶段保持未完成，不得跳过后伪造四模式比较。
- 四种模式使用同一 `dataset_sha256`、同一 Top K 和同一知识库快照。
- 报告不得包含 API Key、数据库 URL、Access Token 或完整 Prompt。
- 质量门失败时命令返回非零退出码，阶段不得标记完成。
- 性能数据必须记录机器、设备和模型配置，不能跨不同环境直接比较。

---

## 文件职责图

| 文件 | 职责 |
|---|---|
| `backend/app/evaluation/comparison.py` | 报告兼容性校验、差异和质量门 |
| `backend/app/evaluation/reporting.py` | 结构化结果渲染为中文 Markdown |
| `backend/scripts/evaluate_rag.py` | `compare` 子命令和退出码 |
| `docs/阶段3验证与演示.md` | 环境准备、验收命令和演示脚本 |
| `docs/阶段3执行进度.md` | 最终状态和验证证据 |

## Task 1：报告比较与兼容性校验

**推荐模型：Terra**

**只读取：** `backend/app/evaluation/schemas.py`、3A 至 3D 的报告 schema、本 Task。

**文件：**

- 新建：`backend/app/evaluation/comparison.py`
- 新建：`backend/tests/unit/test_evaluation_comparison.py`

**接口：**

```python
@dataclass(frozen=True)
class QualityThresholds:
    recall_at_5: float = 0.85
    citation_hit_rate: float = 0.90
    refusal_accuracy: float = 0.90

@dataclass(frozen=True)
class GateResult:
    passed: bool
    failures: list[str]

@dataclass(frozen=True)
class ComparisonReport:
    baseline: EvaluationReport
    modes: dict[str, EvaluationReport]
    metric_deltas: dict[str, dict[str, float]]
    gate: GateResult

def compare_reports(reports: list[EvaluationReport]) -> ComparisonReport: ...
def check_quality_gate(report: EvaluationReport, thresholds: QualityThresholds) -> GateResult: ...
```

- [ ] **Step 1：写相同数据集成功、哈希不一致、schema 不一致和阈值失败测试**
- [ ] **Step 2：实现兼容性校验**

比较前必须校验：`schema_version`、`dataset_sha256`、`case_count` 和 `top_k` 完全一致。失败信息只列字段和值，不输出案例问题全文。

- [ ] **Step 3：实现质量门并验证**

运行：`uv run pytest tests/unit/test_evaluation_comparison.py -q`

- [ ] **Step 4：提交**

提交：`git commit -m "feat: 增加RAG报告比较与质量门"`

## Task 2：中文报告与性能说明

**推荐模型：Terra**

**只读取：** `backend/app/evaluation/comparison.py`、本 Task。

**文件：**

- 新建：`backend/app/evaluation/reporting.py`
- 新建：`backend/tests/unit/test_evaluation_reporting.py`

**接口：**

```python
def render_markdown_report(comparison: ComparisonReport) -> str: ...
```

- [ ] **Step 1：写标题、模式表、退化提示、环境和失败案例 ID 测试**
- [ ] **Step 2：实现固定章节**

输出顺序：执行环境 → 数据集 → 四模式指标表 → 相对基线变化 → 分类指标 → 延迟 P50/P95 → 质量门 → 失败案例 ID → 推荐配置 → 回退配置。

- [ ] **Step 3：确保 Markdown 不包含问题全文和片段全文**
- [ ] **Step 4：验证并提交**

运行：`uv run pytest tests/unit/test_evaluation_reporting.py -q`

提交：`git commit -m "feat: 生成中文RAG质量报告"`

## Task 3：比较命令与自动退出码

**推荐模型：Sol**

**只读取：** `backend/scripts/evaluate_rag.py`、比较与报告模块、对应脚本测试。

**文件：**

- 修改：`backend/scripts/evaluate_rag.py`
- 修改：`backend/tests/unit/test_evaluate_rag_script.py`

**CLI：**

```powershell
uv run python -m scripts.evaluate_rag compare `
  --vector reports/stage3e-vector.json `
  --hybrid reports/stage3e-hybrid.json `
  --rerank reports/stage3e-rerank.json `
  --rewrite reports/stage3e-rewrite.json `
  --output reports/stage3e-comparison.md
```

- [ ] **Step 1：写成功为 0、质量失败为 2、输入错误为 1 的测试**
- [ ] **Step 2：实现 `compare` 子命令，原有单模式命令保持兼容**
- [ ] **Step 3：控制台只输出报告路径、摘要指标和失败原因**
- [ ] **Step 4：运行所有 evaluation 单元测试并提交**

运行：`uv run pytest tests/unit/test_evaluation_*.py tests/unit/test_evaluate_rag_script.py -q`

提交：`git commit -m "feat: 自动执行第三阶段质量门"`

## Task 4：最终验证、演示与收口

**推荐模型：Sol**

**只读取：** 当前阶段 diff、四份 JSON 报告、比较报告、README、学习笔记、执行看板。

**文件：**

- 新建：`docs/阶段3验证与演示.md`
- 修改：`docs/阶段3执行进度.md`
- 修改：`README.md`
- 修改：`docs/学习笔记.md`

- [ ] **Step 1：生成四模式报告和比较报告**

所有命令使用相同数据集、知识库 UUID、Top K 和数据库快照；记录真实返回码。

- [ ] **Step 2：运行完整自动化验证**

```powershell
Set-Location backend
uv run pytest -q
uv run ruff check app tests migrations scripts
uv run ruff format --check app tests migrations scripts
$env:RUN_DATABASE_TESTS='1'
uv run pytest tests/integration -q
Remove-Item Env:RUN_DATABASE_TESTS
uv run alembic downgrade 20260714_04
uv run alembic upgrade head
```

预期：测试和 Ruff 全部通过，迁移可降级并重新升级。

- [ ] **Step 3：编写中文验收和五分钟演示**

演示固定包含：纯向量基线 → 编号关键词问题 → 语义问题 → 多轮指代问题 → 无答案拒答 → 指标对比 → 纯向量回退。

- [ ] **Step 4：Sol 最终审查质量、安全、回退和文档一致性**

只审查第三阶段 Git diff、接口契约、报告摘要和验证输出。发现门槛不达标时保持 3E `进行中`，不得通过修改看板掩盖。

- [ ] **Step 5：更新执行看板并提交**

将 3E 和第三阶段总状态改为 `已完成`，记录四份报告、比较报告、完整测试、Ruff、数据库测试和迁移验证证据。

提交：`git commit -m "docs: 完成第三阶段检索质量验收"`

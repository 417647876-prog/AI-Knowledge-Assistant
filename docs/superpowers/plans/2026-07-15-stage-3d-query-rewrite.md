# 阶段 3D：选择性 Query Rewrite 与多轮检索评估实施计划

> **执行要求：** 使用 `executing-plans` 逐 Task 执行；除非用户明确要求，不派发子代理。所有步骤用本文复选框跟踪。
>
> **当前状态：** 计划已就绪，但阶段 3C 尚未开始。3C 未验收通过前，只允许评审和修订本计划，不得修改 3D 业务代码。

**目标：** 复用阶段 2D 的流式多轮问答，减少不必要的改写模型调用，并让改写失败时安全回退到原问题。

**架构：** 2D 前端继续在 `sessionStorage` 中按用户和知识库隔离历史，并通过 `POST /questions/stream` 发送受限的 `history`。后端继续使用既有 `ConversationMessage`、`QuestionRewriter` 和 `RagService.stream_answer`；本阶段只增加确定性触发规则、改写失败回退和评估模式。

**技术栈：** Python 3.12、FastAPI、Pydantic、既有 ChatProvider、pytest、Vue 3。

## 模型、推理强度与开发强度

推理强度表示执行任务时给模型分配的思考档位；开发强度表示任务本身的跨模块范围、回归风险和验收成本。两者不是同一个概念。

| Task | 工作内容 | 推荐模型 | 推理强度 | 开发强度 | 分配理由 |
|---|---|---|---|---|---|
| 1 | 阶段 2D/3C 集成门禁 | Sol | high | 低 | 代码改动少，但错误判断会导致在错误基线上开发 |
| 2 | 选择性改写纯规则 | Terra | medium | 中 | 边界明确、无 I/O、适合用参数化测试完成 |
| 3 | RagService 改写、回退与原问题回答 | Sol | high | 高 | 跨改写、检索、Prompt、SSE 与异常语义，是 3D 主链路 |
| 4 | SSE 契约、前端状态与展示回归 | Terra | medium | 中 | 契约固定，主要是 TypeScript 类型、Pinia 状态和回归测试 |
| 5 | 评估模式、真实报告与质量门 | Sol | xhigh | 高 | 涉及评估口径、真实模型波动、指标对比和阶段是否通过的决策 |

### 模型切换规则

1. 每个 Task 从表中指定的模型和推理强度开始，不在同一个 Task 中随意切换。
2. Terra 在同一条验证上连续失败两次，停止试错，切换为 `Sol｜high`；如果失败横跨评估口径或多模块根因分析，切换为 `Sol｜xhigh`。
3. Task 3 和 Task 5 不下调为 Terra。它们分别控制生产主链路和质量门，节省的模型成本不足以覆盖回归风险。
4. 单纯格式、文档措辞或明确的测试夹具修复可临时降为 `Terra｜low`，但不得借此改变 Task 的主执行模型。
5. 每次切换都写入 `docs/阶段3执行进度.md`，记录原模型、目标模型、失败验证和切换原因。

## 全局约束

- 3C 未验收通过时不得开始。
- 当前分支必须包含阶段 2D 的 `ConversationMessage`、`QuestionRewriter`、流式问答接口和前端 `buildHistory`。
- 不新增服务端会话表、消息表、`conversation_id` 或数据库历史读取服务。
- 不允许客户端绕过 2D 的历史长度、消息角色和内容长度校验。
- 首轮完整问题不调用改写模型。
- 改写问题只用于检索，最终回答仍以用户原问题为语义主体。
- 改写异常、超时或空文本时回退原问题，并通过流式事件说明真实使用的检索问题。
- `rewrite` SSE 事件固定包含 `standalone_question`、`elapsed_ms` 和 `used_fallback`；不得包含历史消息原文。
- 只捕获并回退 `AppError(code="QUESTION_REWRITE_ERROR")`；权限、知识库不存在和其他编程错误继续按原错误链路处理。
- 普通自动化测试使用 Fake Provider，不访问外部模型；真实质量门使用显式 CLI 执行。

---

## 文件职责图

| 文件 | 职责 |
|---|---|
| `backend/app/ai/contracts.py` | 已有 `ConversationMessage` 和 `QuestionRewriter` 契约 |
| `backend/app/ai/rewrite.py` | 改写 Prompt、结果校验和新增选择性触发规则 |
| `backend/app/rag/service.py` | 原问题、检索问题、回退和流式事件编排 |
| `backend/app/api/v1/questions.py` | 保持既有 history 校验和依赖构造 |
| `frontend/src/stores/conversationStorage.ts` | 已有历史裁剪与请求历史构建 |
| `frontend/src/types/conversation.ts` | `rewrite` 事件和回答状态类型 |
| `frontend/src/stores/conversations.ts` | 保存实际检索问题和回退标记 |
| `frontend/src/components/RetrievalDetails.vue` | 在检索详情中显示是否发生回退 |
| `backend/scripts/evaluate_rag.py` | 多轮分类的 rewrite 评估模式 |

## 执行顺序与完成口径

```text
3C 验收通过
  -> Task 1 确认 2D/3C 边界仍存在
  -> Task 2 完成 should_rewrite 纯规则
  -> Task 3 接入 RagService 并完成安全回退
  -> Task 4 固化 SSE/前端契约
  -> Task 5 生成真实报告并通过质量门
```

每个 Task 都必须经历“失败测试 → 最小实现 → 目标测试 → Ruff/格式检查 → 独立提交”。只有 Task 5 的真实报告、多轮 Recall@5 门槛和完整回归全部通过，阶段 3D 才能标记为完成。

## Task 1：阶段 2D 集成门禁

**推荐模型：Sol｜推理强度：high｜开发强度：低**

**只读取：** `backend/app/ai/contracts.py`、`backend/app/ai/rewrite.py`、`backend/app/api/v1/questions.py`、`backend/app/rag/service.py`、`frontend/src/stores/conversationStorage.ts`、执行看板。

**文件：**

- 修改：`docs/阶段3执行进度.md`

- [x] **Step 1：确认 3C 已完成，以及 2D 的四个既有边界仍存在**

先读取 `docs/阶段3执行进度.md`，必须同时看到“3C 已完成”和“3D 未开始”。如果 3C 不是已完成状态，停止本 Task，把 3D 保持为未开始，不修改业务代码。

运行：

```powershell
rg -n "ConversationMessage|QuestionRewriter|questions/stream|buildHistory" backend frontend -g '*.py' -g '*.ts' -g '*.vue'
```

预期：命中 `backend/app/ai/contracts.py`、`backend/app/ai/rewrite.py`、`backend/app/api/v1/questions.py`、`backend/app/rag/service.py` 和 `frontend/src/stores/conversationStorage.ts`；四个边界缺少任意一个都视为门禁失败。

- [x] **Step 2：确认不新增数据库历史**

`backend/app/db/models` 中不得新增会话或消息实体；历史输入必须继续是 `StreamQuestionRequest.history`，并由现有角色、条数和长度校验保护。

- [x] **Step 3：更新看板**

3C 已完成且四个既有边界全部存在时，将 3D Task 1 标记为 `已完成` 并记录文件路径；缺失时将 3D 标记为 `阻塞`，下一步写“同步阶段 2D/3C 基线”，不得在 3D 重建 2D 或绕开 3C。

- [x] **Step 4：提交门禁结果**

```powershell
git add docs/阶段3执行进度.md
git commit -m "docs: 确认阶段3D开发门禁"
```

## Task 2：选择性改写规则

**推荐模型：Terra｜推理强度：medium｜开发强度：中**

**只读取：** `backend/app/ai/contracts.py`、`backend/app/ai/rewrite.py`、`backend/tests/unit/test_question_rewriter.py`。

**文件：**

- 修改：`backend/app/ai/rewrite.py`
- 修改：`backend/tests/unit/test_question_rewriter.py`

**接口：**

```python
def should_rewrite(question: str, history: list[ConversationMessage]) -> bool: ...
```

- [x] **Step 1：写首轮、完整问题、短追问和指代词测试**

无历史时永不改写；有历史且问题不超过 12 个字符时改写；有历史且包含 `它`、`这个`、`那个`、`上述`、`前面`、`该制度`、`怎么办` 或 `呢` 时改写；其他完整问题不改写。

测试使用参数化用例覆盖下列输入，不调用模型：

```python
@pytest.mark.parametrize(
    ("question", "has_history", "expected"),
    [
        ("它有什么缺点？", False, False),
        ("它有什么缺点？", True, True),
        ("多久更新一次？", True, True),
        ("上述制度如何申请？", True, True),
        ("员工入职满一年有多少天带薪年假？", True, False),
    ],
)
def test_should_rewrite_is_selective(
    question: str,
    has_history: bool,
    expected: bool,
) -> None:
    history = (
        [
            ConversationMessage(role="user", content="介绍相关制度。"),
            ConversationMessage(role="assistant", content="这是制度摘要。"),
        ]
        if has_history
        else []
    )

    assert should_rewrite(question, history) is expected
```

先运行：`uv run pytest tests/unit/test_question_rewriter.py -q`

预期：FAIL，原因是无法从 `app.ai.rewrite` 导入 `should_rewrite`。

- [x] **Step 2：实现纯规则并验证**

在 `backend/app/ai/rewrite.py` 增加唯一策略入口：

```python
_REWRITE_MARKERS = (
    "它",
    "这个",
    "那个",
    "上述",
    "前面",
    "该制度",
    "怎么办",
    "呢",
)


def should_rewrite(question: str, history: list[ConversationMessage]) -> bool:
    question = question.strip()
    if not history or not question:
        return False
    return len(question) <= 12 or any(marker in question for marker in _REWRITE_MARKERS)
```

运行：`uv run pytest tests/unit/test_question_rewriter.py -q`

预期：全部通过；现有 Prompt 注入防护、空结果和 Provider 异常包装测试不得退化。

- [x] **Step 3：提交**

提交：`git commit -m "feat: 仅在需要时改写多轮问题"`

## Task 3：RagService 回退和流式事件

**推荐模型：Sol｜推理强度：high｜开发强度：高**

**只读取：** `backend/app/rag/service.py`、`backend/app/ai/rewrite.py`、`backend/app/rag/streaming.py`、`backend/tests/unit/test_rag_service.py`。

**文件：**

- 修改：`backend/app/rag/service.py`
- 修改：`backend/tests/unit/test_rag_service.py`
- 修改：`backend/tests/unit/test_rag_streaming.py`

**新增内部应用接口：**

```python
async def answer_with_retrieval_question(
    self,
    knowledge_base_id: UUID,
    original_question: str,
    retrieval_question: str,
    top_k: int,
) -> QuestionAnswer: ...
```

`answer()` 使用相同的原问题和检索问题调用该方法，保持普通问答兼容；Task 5 的评估适配器使用不同的两个问题调用该方法，确保 Recall 和引用来自同一个实际检索问题。该方法属于后端应用内部接口，不新增 HTTP API。

- [x] **Step 1：写不触发、成功改写、改写异常回退和原问题回答测试**

关键断言：规则不触发时不会调用 `QuestionRewriter`；触发后 Retriever 收到独立问题；`build_rag_prompt` 收到原问题；`QUESTION_REWRITE_ERROR` 时 Retriever 收到原问题且流继续结束。

为 `backend/tests/unit/test_rag_service.py` 增加只抛指定错误的替身：

```python
class FailingRewriter:
    def __init__(self, code: str = "QUESTION_REWRITE_ERROR") -> None:
        self.code = code

    async def rewrite(
        self,
        history: list[ConversationMessage],
        question: str,
    ) -> str:
        raise AppError(code=self.code, message="改写失败", status_code=502)
```

至少新增四组断言：

```python
assert rewriter.calls == []
assert retriever.calls[0]["query"] == "向量检索有什么缺点？"
assert rewrite_event.data["used_fallback"] is True
assert fallback_retriever.calls[0]["query"] == "它有什么缺点？"
```

另用 `monkeypatch` 记录 `build_rag_prompt` 的 `question` 参数，确认检索使用独立问题，但回答 Prompt 收到去除首尾空白后的用户原问题。

为 `answer_with_retrieval_question` 增加测试：Retriever 收到 `retrieval_question`，`build_rag_prompt` 收到 `original_question`，返回引用只能映射该次检索得到的片段。

- [x] **Step 2：实现 `policy -> rewrite or original -> retrieve -> answer`**

回退只捕获 `AppError(code="QUESTION_REWRITE_ERROR")`。实现时保留两个明确变量：`original_question` 供回答使用，`standalone_question` 供 Embedding 和 Retriever 使用。`rewrite` 流事件必须包含实际使用的 `standalone_question`、`elapsed_ms` 和 `used_fallback: bool`；不得暴露原始历史文本。

主流程必须等价于：

```python
original_question = question.strip()
standalone_question = original_question
used_fallback = False
rewrite_ms = 0

if should_rewrite(original_question, history):
    yield StreamEvent("status", {"phase": "rewriting"})
    rewrite_started = perf_counter()
    try:
        standalone_question = await self._question_rewriter.rewrite(
            history,
            original_question,
        )
    except AppError as error:
        if error.code != "QUESTION_REWRITE_ERROR":
            raise
        used_fallback = True
    rewrite_ms = _elapsed_ms(rewrite_started)

yield StreamEvent(
    "rewrite",
    {
        "standalone_question": standalone_question,
        "elapsed_ms": rewrite_ms,
        "used_fallback": used_fallback,
    },
)
```

后续 `_retrieve` 必须使用 `standalone_question`；`build_rag_prompt` 必须使用 `original_question`。不要捕获 `Exception`，也不要把改写错误转成“回答失败”。

- [x] **Step 3：运行目标测试并提交**

运行：`uv run pytest tests/unit/test_rag_service.py tests/unit/test_rag_streaming.py tests/unit/test_question_rewriter.py -q`

再运行：`uv run ruff check app tests/unit/test_rag_service.py tests/unit/test_rag_streaming.py tests/unit/test_question_rewriter.py` 和 `uv run ruff format --check app tests/unit/test_rag_service.py tests/unit/test_rag_streaming.py tests/unit/test_question_rewriter.py`。

提交：`git commit -m "feat: 为问题改写增加安全回退"`

## Task 4：流式 API 与前端展示回归

**推荐模型：Terra｜推理强度：medium｜开发强度：中**

**只读取：** `backend/app/api/v1/questions.py`、`frontend/src/api/questions.ts`、`frontend/src/stores/conversations.ts`、已有流式 API 和 Store 测试。

**文件：**

- 修改：`backend/tests/integration/test_question_api.py`
- 修改：`frontend/src/types/conversation.ts`
- 修改：`frontend/src/stores/conversations.ts`
- 修改：`frontend/src/stores/conversations.spec.ts`
- 修改：`frontend/src/components/RetrievalDetails.vue`

- [ ] **Step 1：写 `used_fallback` 事件解析和会话状态更新测试**

后端 Stub 的事件固定为：

```python
yield StreamEvent(
    "rewrite",
    {
        "standalone_question": "它呢？",
        "elapsed_ms": 10,
        "used_fallback": True,
    },
)
```

前端 Store 测试固定断言：

```typescript
expect(store.messages[store.messages.length - 1]).toMatchObject({
  kind: 'assistant',
  standaloneQuestion: '它呢？',
  rewriteUsedFallback: true,
})
```

先分别运行后端集成测试和前端 Store 测试，预期前者因旧 Stub 契约断言失败，后者因 `used_fallback` 类型或 `rewriteUsedFallback` 状态尚不存在而失败。

- [ ] **Step 2：在既有 rewrite 事件类型中增加 `used_fallback`，不新增接口路径或 history 字段**

类型和状态只做下列扩展：

```typescript
export interface AssistantMessage {
  id: string
  kind: 'assistant'
  questionId: string
  content: string
  createdAt: string
  status: AssistantStatus
  phase: 'rewriting' | 'retrieving' | 'generating' | null
  citations: Citation[]
  standaloneQuestion: string | null
  rewriteUsedFallback?: boolean
  retrievedChunkCount: number | null
  timings: StreamTimings | null
  errorCode: string | null
  requestId: string | null
}

export type QuestionStreamEvent =
  | { event: 'status'; data: { phase: 'rewriting' | 'retrieving' | 'generating' } }
  | {
      event: 'rewrite'
      data: {
        standalone_question: string
        elapsed_ms: number
        used_fallback: boolean
      }
    }
  | { event: 'retrieval'; data: { retrieved_chunk_count: number; elapsed_ms: number } }
  | { event: 'token'; data: { delta: string } }
  | { event: 'citation'; data: Citation }
  | {
      event: 'done'
      data: {
        request_id: string
        citations: Citation[]
        retrieved_chunk_count: number
        timings: StreamTimings
      }
    }
  | { event: 'error'; data: { code: string; message: string; request_id: string } }
```

`conversations.ts` 在收到 `rewrite` 时写入：

```typescript
answer.standaloneQuestion = event.data.standalone_question
answer.rewriteUsedFallback = event.data.used_fallback
```

`RetrievalDetails.vue` 增加一行只读说明：回退时显示“已回退到原问题”，否则显示“未回退”。字段使用可选布尔值，保证旧 `sessionStorage` 会话仍可读取，不做存储迁移。

- [ ] **Step 3：验证前端与后端**

运行：`Set-Location backend; uv run pytest tests/integration/test_question_api.py -q`

运行：`Set-Location ../frontend; npm.cmd test -- --run src/stores/conversations.spec.ts src/api/questions.spec.ts`

再运行：`npm.cmd run build`，预期 TypeScript 编译和 Vite 构建通过。

- [ ] **Step 4：提交**

提交：`git commit -m "test: 覆盖问题改写回退体验"`

## Task 5：3D 评估与验收

**推荐模型：Sol｜推理强度：xhigh｜开发强度：高**

**只读取：** 当前阶段 diff、`backend/scripts/evaluate_rag.py`、3A 数据集、执行看板。

**文件：**

- 修改：`backend/scripts/evaluate_rag.py`
- 修改：`backend/tests/unit/test_evaluate_rag_script.py`
- 修改：`backend/app/evaluation/runner.py`
- 修改：`backend/tests/unit/test_evaluation_runner.py`
- 修改：`docs/阶段3执行进度.md`
- 修改：`README.md`

- [ ] **Step 1：先写 rewrite 模式的失败测试**

必须覆盖：

1. `parse_args` 接受 `--mode rewrite`。
2. 只有 `category == "multi_turn"` 的案例使用 `EvaluationCase.history`。
3. `EvaluationTurn` 被转换为 `ConversationMessage`，角色和顺序保持不变。
4. 无历史或完整问题不调用 `QuestionRewriter`。
5. 报告中的 `mode` 为 `rewrite`，并继续排除数据库连接串和 API Key。

运行：`uv run pytest tests/unit/test_evaluate_rag_script.py tests/unit/test_evaluation_runner.py -q`

预期：FAIL，`parse_args` 尚不接受 `rewrite`，评估链路也尚未消费 history。

- [ ] **Step 2：增加 `--mode rewrite`，仅为 `multi_turn` 案例提供 2D 格式 history**

CLI 的 `choices` 扩为 `vector`、`hybrid`、`rerank`、`rewrite`。`rewrite` 表示“使用 3C 已验收的 Retriever/Reranker 配置，并在多轮案例前执行选择性改写”，不能把字符串 `rewrite` 直接赋给只接受检索后端模式的 `rag_retrieval_mode`。

评估 Runner 必须在 Embedding 和 Retriever 之前得到每个案例的实际检索问题，并把它记录到案例结果或测试可观察的调用中；回答仍使用 `case.question` 作为原问题。禁止先用原问题算 Recall、再只对回答路径做改写，否则报告会虚假通过。

新增明确协议，避免把改写逻辑散落到 CLI：

```python
class EvaluationQueryResolver(Protocol):
    async def resolve(self, case: EvaluationCase) -> str: ...
```

同时把评估回答协议改为显式接收已解析的检索问题：

```python
class EvaluationAnswerer(Protocol):
    async def answer_case(
        self,
        *,
        knowledge_base_id: UUID,
        case: EvaluationCase,
        retrieval_question: str,
        top_k: int,
    ) -> QuestionAnswer: ...
```

`evaluate_cases` 对每条案例只调用一次 `query_resolver.resolve(case)`，随后把返回值同时传给 Embedding、Retriever 和 `answer_case`。`RagServiceEvaluationAnswerer` 调用 Task 3 新增的 `answer_with_retrieval_question(case.question, retrieval_question)`，保证最终 Prompt 仍使用原问题。

生产 Resolver 复用 `should_rewrite` 和现有 `QuestionRewriter`；仅当 `case.category == "multi_turn"` 且规则触发时才转换并传入 `case.history`。不触发或捕获 `QUESTION_REWRITE_ERROR` 时返回 `case.question.strip()`。这样真实模型每个案例只改写一次，不会出现评估 Retriever 和回答链路分别改写、得到两个不同问题的情况。

- [ ] **Step 3：生成未改写和选择性改写两份报告**

先生成未改写对照报告，配置必须与 rewrite 报告一致，唯一变量是是否启用改写。报告文件均为本地产物，不提交 Git：

```powershell
if (-not $env:EVALUATION_KNOWLEDGE_BASE_ID) {
  throw "请先在当前 PowerShell 会话设置 EVALUATION_KNOWLEDGE_BASE_ID。"
}
uv run python -m scripts.evaluate_rag `
  --dataset tests/fixtures/evaluation/stage3.jsonl `
  --knowledge-base-id $env:EVALUATION_KNOWLEDGE_BASE_ID `
  --mode rerank `
  --output reports/stage3d-no-rewrite.json
```

运行：`uv run python -m scripts.evaluate_rag --dataset tests/fixtures/evaluation/stage3.jsonl --knowledge-base-id $env:EVALUATION_KNOWLEDGE_BASE_ID --mode rewrite --output reports/stage3d-rewrite.json`

- [ ] **Step 4：验证质量门**

采用上限感知质量门：rewrite 多轮 Recall@5 必须达到 `min(100%, no-rewrite 多轮 Recall@5 + 15 个百分点)`；无历史和完整问题的改写调用次数为 0；所有改写失败案例均安全回退。若未改写基线已经达到 100%，只要求保持 100%，不要求不可能的 115%。

计算口径固定为：

```text
多轮 Recall@5 = multi_turn 案例 recall_at_k 的算术平均值
目标 Recall@5 = min(1.0, no-rewrite 多轮 Recall@5 + 0.15)
通过条件 = rewrite 多轮 Recall@5 >= 目标 Recall@5
```

目标值必须调用 3C 已加入 `backend/app/evaluation/metrics.py` 的 `ceiling_aware_target(no_rewrite_recall, 0.15)` 计算，不在 CLI 中复制第二套公式。示例：未改写基线为 50% 时目标为 65%；基线为 93% 时目标为 100%；基线为 100% 时目标仍为 100%。同时要求总体引用命中率和拒答准确率均不得低于 3C 正式报告；如果未达到，不得只调整文档数字，必须保留两份报告并用 `Sol｜xhigh` 分析失败案例。

- [ ] **Step 5：运行完整测试、更新看板并提交**

后端验证：

```powershell
Set-Location backend
uv run pytest -q
uv run ruff check app tests migrations scripts
uv run ruff format --check app tests migrations scripts
```

数据库集成测试必须使用临时空库，沿用 README 中“创建临时库 → Alembic 升级 → `RUN_DATABASE_TESTS=1` → 测试 → finally 删除临时库”的命令，不复用开发库。

前端验证：

```powershell
Set-Location ../frontend
npm.cmd test -- --run
npm.cmd run build
```

通过后将 3D 标为 `已完成`，把下一步写为“另开任务开始 3E Task 1”；不要在同一提交中提前执行 3E 代码。

提交：`git commit -m "docs: 完成阶段3D选择性改写验收"`

## 计划自检

- [x] 范围：只增强阶段 2D 的选择性改写、回退、事件和评估，不新增服务端会话存储。
- [x] 前置：3D Task 1 已确认 3C 风险豁免记录与阶段 2D 集成边界，后续可从 Task 2 进入业务代码。
- [x] 模型：每个 Task 只指定一个 Sol 或 Terra，并附推理强度和开发强度。
- [x] 回退：只回退 `QUESTION_REWRITE_ERROR`，其他错误不被吞掉。
- [x] 语义：独立问题只用于检索，最终 Prompt 使用用户原问题。
- [x] 评估：Recall 使用实际改写后的检索问题，不允许只改回答路径。
- [x] 质量门：相对提升采用 `min(100%, 基线 + 目标提升百分点)`，高基线不会形成不可达门槛。
- [x] 兼容：不新增 API 路径或 history 字段，旧浏览器会话无需迁移。

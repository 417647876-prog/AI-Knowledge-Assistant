# 阶段 3D：选择性 Query Rewrite 与多轮检索评估实施计划

> **执行要求：** 使用 `executing-plans` 逐 Task 执行；除非用户明确要求，不派发子代理。所有步骤用本文复选框跟踪。

**目标：** 复用阶段 2D 的流式多轮问答，减少不必要的改写模型调用，并让改写失败时安全回退到原问题。

**架构：** 2D 前端继续在 `sessionStorage` 中按用户和知识库隔离历史，并通过 `POST /questions/stream` 发送受限的 `history`。后端继续使用既有 `ConversationMessage`、`QuestionRewriter` 和 `RagService.stream_answer`；本阶段只增加确定性触发规则、改写失败回退和评估模式。

**技术栈：** Python 3.12、FastAPI、Pydantic、既有 ChatProvider、pytest、Vue 3。

## 全局约束

- 3C 必须已验收通过，或已有明确、可审计的质量门风险豁免记录。当前 3C 于 2026-07-15 由用户明确接受 MRR 门未通过的风险并完成收尾；该豁免只解除 3D 的前置流程阻塞，不豁免 3D 自身质量门。
- 当前分支必须包含阶段 2D 的 `ConversationMessage`、`QuestionRewriter`、流式问答接口和前端 `buildHistory`。
- 不新增服务端会话表、消息表、`conversation_id` 或数据库历史读取服务。
- 不允许客户端绕过 2D 的历史长度、消息角色和内容长度校验。
- 首轮完整问题不调用改写模型。
- 改写问题只用于检索，最终回答仍以用户原问题为语义主体。
- 改写异常、超时或空文本时回退原问题，并通过流式事件说明真实使用的检索问题。

---

## 文件职责图

| 文件 | 职责 |
|---|---|
| `backend/app/ai/contracts.py` | 已有 `ConversationMessage` 和 `QuestionRewriter` 契约 |
| `backend/app/ai/rewrite.py` | 改写 Prompt、结果校验和新增选择性触发规则 |
| `backend/app/rag/service.py` | 原问题、检索问题、回退和流式事件编排 |
| `backend/app/api/v1/questions.py` | 保持既有 history 校验和依赖构造 |
| `frontend/src/stores/conversationStorage.ts` | 已有历史裁剪与请求历史构建 |
| `backend/scripts/evaluate_rag.py` | 多轮分类的 rewrite 评估模式 |

## Task 1：阶段 2D 集成门禁

**推荐模型：Sol**

**只读取：** `backend/app/ai/contracts.py`、`backend/app/ai/rewrite.py`、`backend/app/api/v1/questions.py`、`backend/app/rag/service.py`、`frontend/src/stores/conversationStorage.ts`、执行看板。

**文件：**

- 修改：`docs/阶段3执行进度.md`

- [ ] **Step 1：确认 2D 的四个既有边界**

运行：

```powershell
rg -n "ConversationMessage|QuestionRewriter|questions/stream|buildHistory" backend frontend -g '*.py' -g '*.ts' -g '*.vue'
```

- [ ] **Step 2：确认不新增数据库历史**

`backend/app/db/models` 中不得新增会话或消息实体；历史输入必须继续是 `StreamQuestionRequest.history`，并由现有角色、条数和长度校验保护。

- [ ] **Step 3：更新看板**

四个既有边界全部存在时，将 3D Task 1 标记为 `已完成` 并记录文件路径；缺失时将 3D 标记为 `阻塞`，下一步写“同步阶段 2D 分支”，不得在 3D 重建 2D。

## Task 2：选择性改写规则

**推荐模型：Terra**

**只读取：** `backend/app/ai/contracts.py`、`backend/app/ai/rewrite.py`、`backend/tests/unit/test_question_rewriter.py`。

**文件：**

- 修改：`backend/app/ai/rewrite.py`
- 修改：`backend/tests/unit/test_question_rewriter.py`

**接口：**

```python
def should_rewrite(question: str, history: list[ConversationMessage]) -> bool: ...
```

- [ ] **Step 1：写首轮、完整问题、短追问和指代词测试**

无历史时永不改写；有历史且问题不超过 12 个字符时改写；有历史且包含 `它`、`这个`、`那个`、`上述`、`前面`、`该制度`、`怎么办` 或 `呢` 时改写；其他完整问题不改写。

- [ ] **Step 2：实现纯规则并验证**

运行：`uv run pytest tests/unit/test_question_rewriter.py -q`

- [ ] **Step 3：提交**

提交：`git commit -m "feat: 仅在需要时改写多轮问题"`

## Task 3：RagService 回退和流式事件

**推荐模型：Sol**

**只读取：** `backend/app/rag/service.py`、`backend/app/ai/rewrite.py`、`backend/app/rag/streaming.py`、`backend/tests/unit/test_rag_service.py`。

**文件：**

- 修改：`backend/app/rag/service.py`
- 修改：`backend/tests/unit/test_rag_service.py`
- 修改：`backend/tests/unit/test_rag_streaming.py`

- [ ] **Step 1：写不触发、成功改写、改写异常回退和原问题回答测试**

关键断言：规则不触发时不会调用 `QuestionRewriter`；触发后 Retriever 收到独立问题；`build_rag_prompt` 收到原问题；`QUESTION_REWRITE_ERROR` 时 Retriever 收到原问题且流继续结束。

- [ ] **Step 2：实现 `policy -> rewrite or original -> retrieve -> answer`**

回退只捕获 `AppError(code="QUESTION_REWRITE_ERROR")`。`rewrite` 流事件必须包含实际使用的 `standalone_question` 和 `used_fallback: bool`；不得暴露原始历史文本。

- [ ] **Step 3：运行目标测试并提交**

运行：`uv run pytest tests/unit/test_rag_service.py tests/unit/test_rag_streaming.py tests/unit/test_question_rewriter.py -q`

提交：`git commit -m "feat: 为问题改写增加安全回退"`

## Task 4：流式 API 与前端展示回归

**推荐模型：Terra**

**只读取：** `backend/app/api/v1/questions.py`、`frontend/src/api/questions.ts`、`frontend/src/stores/conversations.ts`、已有流式 API 和 Store 测试。

**文件：**

- 修改：`backend/tests/integration/test_question_api.py`
- 修改：`frontend/src/types/conversation.ts`
- 修改：`frontend/src/stores/conversations.ts`
- 修改：`frontend/src/stores/conversations.spec.ts`

- [ ] **Step 1：写 `used_fallback` 事件解析和会话状态更新测试**
- [ ] **Step 2：在既有 rewrite 事件类型中增加 `used_fallback`，不新增接口路径或 history 字段**
- [ ] **Step 3：验证前端与后端**

运行：`Set-Location backend; uv run pytest tests/integration/test_question_api.py -q`

运行：`Set-Location ../frontend; npm.cmd test -- --run src/stores/conversations.spec.ts src/api/questions.spec.ts`

- [ ] **Step 4：提交**

提交：`git commit -m "test: 覆盖问题改写回退体验"`

## Task 5：3D 评估与验收

**推荐模型：Sol**

**只读取：** 当前阶段 diff、`backend/scripts/evaluate_rag.py`、3A 数据集、执行看板。

**文件：**

- 修改：`backend/scripts/evaluate_rag.py`
- 修改：`backend/tests/unit/test_evaluate_rag_script.py`
- 修改：`docs/阶段3执行进度.md`
- 修改：`README.md`

- [ ] **Step 1：增加 `--mode rewrite`，仅为 `multi_turn` 案例提供 2D 格式 history**
- [ ] **Step 2：生成未改写和选择性改写两份报告**

运行：`uv run python -m scripts.evaluate_rag --dataset tests/fixtures/evaluation/stage3.jsonl --knowledge-base-id $env:EVALUATION_KNOWLEDGE_BASE_ID --mode rewrite --output reports/stage3d-rewrite.json`

- [ ] **Step 3：验证质量门**

多轮分类 Recall@5 相对未改写结果提升至少 15 个百分点；无历史和完整问题的改写调用次数为 0；所有改写失败案例均安全回退。

- [ ] **Step 4：运行完整测试、更新看板并提交**

通过后将 3D 标为 `已完成`、3E Task 1 标为 `进行中`。

提交：`git commit -m "docs: 完成阶段3D选择性改写验收"`


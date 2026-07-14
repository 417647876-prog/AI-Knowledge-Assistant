# 阶段 3D：Query Rewrite 与多轮问题改写实施计划

> **执行要求：** 使用 `executing-plans` 逐 Task 执行；除非用户明确要求，不派发子代理。所有步骤用本文复选框跟踪。

**目标：** 只对依赖上下文或不适合直接检索的问题进行改写，提高多轮检索质量，并在任何改写故障时安全回退原问题。

**架构：** 2D 会话模块负责提供已授权的最近历史；`RewritePolicy` 判断是否需要模型；`QueryRewriter` 生成独立检索问题；`RagService` 始终保留原问题用于回答。

**技术栈：** Python 3.12、FastAPI、Pydantic、现有 ChatProvider、pytest。

## 全局约束

- 3C 未验收通过时不得开始。
- 当前 `main` 未发现 2D 聊天历史代码；Task 1 未通过时，本阶段必须标记为 `阻塞`。
- 不允许客户端提交任意完整聊天历史绕过服务端会话权限。
- 首轮完整问题不调用改写模型。
- 改写问题只用于检索，原问题用于最终回答。
- 历史轮数和字符数必须同时限制。
- 改写失败、超时、空文本时回退原问题。

---

## 文件职责图

| 文件 | 职责 |
|---|---|
| `backend/app/rag/schemas.py` | `ConversationTurn` 和改写结果 |
| `backend/app/rag/rewrite_policy.py` | 确定性触发规则 |
| `backend/app/rag/query_rewriter.py` | Prompt、模型调用、输出校验 |
| `backend/app/rag/contracts.py` | `ConversationHistoryReader` 与 `QueryRewriter` 协议 |
| `backend/app/chat/rag_history.py` | 2D 数据模型到 RAG 历史的权限安全适配器 |
| `backend/app/rag/service.py` | 原问题、检索问题和回退编排 |

## Task 1：2D 同步与接口门禁

**推荐模型：Sol**

**只读取：** `git log -20`、`backend/app/db/models`、`backend/app/api/v1`、`frontend/src` 中包含 conversation/history/message 的文件、执行看板。

**文件：**

- 修改：`docs/阶段3执行进度.md`

- [ ] **Step 1：检查 2D 必需能力**

运行：

```powershell
rg -n "class Conversation|class .*Message|conversation_id|chat history|聊天历史" backend frontend docs -g '*.py' -g '*.ts' -g '*.vue' -g '*.md'
git log --oneline --all -20
```

- [ ] **Step 2：验证四个前置条件**

必须同时找到：会话表、消息表、按当前用户和知识库授权的历史读取服务、前端当前会话 ID。任何一项缺失都不得自行补做 2D。

- [ ] **Step 3：更新看板**

全部存在：记录实际类名和文件路径，将 3D Task 1 标记为 `已完成`。存在缺项：将 3D 标记为 `阻塞`，下一步明确写“先同步或完成阶段 2D”，本阶段停止。

## Task 2：内部历史契约与触发规则

**推荐模型：Terra**

**只读取：** 2D 实际历史 DTO、`backend/app/rag/schemas.py`、本 Task。

**文件：**

- 修改：`backend/app/rag/schemas.py`
- 修改：`backend/app/rag/contracts.py`
- 新建：`backend/app/rag/rewrite_policy.py`
- 新建：`backend/tests/unit/test_rewrite_policy.py`

**接口：**

```python
@dataclass(frozen=True)
class ConversationTurn:
    role: Literal["user", "assistant"]
    content: str

class ConversationHistoryReader(Protocol):
    async def recent_turns(
        self, *, conversation_id: UUID, knowledge_base_id: UUID,
        user_id: UUID, limit: int,
    ) -> list[ConversationTurn]: ...

def should_rewrite(question: str, history: list[ConversationTurn]) -> bool: ...
```

- [ ] **Step 1：写首轮、完整问题、短问题和指代词测试**

触发指代词固定为：`它`、`这个`、`那个`、`上述`、`前面`、`该制度`、`怎么办`、`呢`。无历史永不触发；有历史且问题长度不超过 12 个字符时触发；有历史且包含指代词时触发。

- [ ] **Step 2：实现纯规则并验证**

运行：`uv run pytest tests/unit/test_rewrite_policy.py -q`

- [ ] **Step 3：提交**

提交：`git commit -m "feat: 定义多轮改写触发规则"`

## Task 3：QueryRewriter 与受限 Prompt

**推荐模型：Terra**

**只读取：** `backend/app/ai/contracts.py`、`backend/app/rag/prompt.py`、`backend/app/ai/chat.py`、对应测试。

**文件：**

- 新建：`backend/app/rag/query_rewriter.py`
- 新建：`backend/tests/unit/test_query_rewriter.py`
- 修改：`backend/app/core/config.py`
- 修改：`backend/tests/unit/test_config.py`

**接口：**

```python
class QueryRewriter(Protocol):
    async def rewrite(
        self, question: str, history: list[ConversationTurn]
    ) -> str: ...

class ChatQueryRewriter:
    def __init__(self, chat_provider: ChatProvider, *, max_history_chars: int) -> None: ...
```

**配置：**

```python
rag_query_rewrite_enabled: bool = False
rag_query_rewrite_history_turns: int = Field(default=6, ge=1, le=20)
rag_query_rewrite_history_chars: int = Field(default=2000, ge=100, le=10000)
```

- [ ] **Step 1：写历史裁剪、Prompt 内容、空响应和异常测试**
- [ ] **Step 2：实现从最新历史向前裁剪，最终恢复时间顺序**
- [ ] **Step 3：Prompt 只允许输出一行独立检索问题**

系统要求固定包含：不得回答问题、不得添加事实、保留专有名词和编号、输出单行问题。输出 `strip()` 后为空或超过问题最大长度时抛出 `QUERY_REWRITE_ERROR`。

- [ ] **Step 4：验证并提交**

运行：`uv run pytest tests/unit/test_query_rewriter.py tests/unit/test_config.py -q`

提交：`git commit -m "feat: 增加受限多轮问题改写器"`

## Task 4：2D 历史适配与 API 接线

**推荐模型：Sol**

**只读取：** Task 1 记录的 2D 会话/消息/权限文件、`backend/app/api/v1/questions.py`、`backend/app/authorization/service.py`。

**文件：**

- 新建：`backend/app/chat/rag_history.py`
- 修改：`backend/app/api/v1/questions.py`
- 新建：`backend/tests/integration/test_question_history_permissions.py`
- 修改：`backend/tests/integration/test_question_api.py`

- [ ] **Step 1：写当前用户可读、其他用户 404、跨知识库 404 和最近 N 轮排序测试**
- [ ] **Step 2：实现 `ConversationHistoryReader` 适配器**

适配器只查询已授权会话；数据库先按消息时间倒序限制数量，再恢复正序。若 2D 实际类名不同，只在适配器 import 和字段映射中适配，不重命名或重写 2D 模块。

- [ ] **Step 3：QuestionRequest 只接受 2D 的 `conversation_id`，不接受 history 数组**
- [ ] **Step 4：运行权限测试并提交**

运行：`$env:RUN_DATABASE_TESTS='1'; uv run pytest tests/integration/test_question_history_permissions.py tests/integration/test_resource_permissions.py -q; Remove-Item Env:RUN_DATABASE_TESTS`

提交：`git commit -m "feat: 安全读取会话历史用于RAG"`

## Task 5：RagService 改写、回退与验收

**推荐模型：Sol**

**只读取：** `backend/app/rag/service.py`、本阶段新增 RAG 文件、RagService/API 测试、评估脚本。

**文件：**

- 修改：`backend/app/rag/service.py`
- 修改：`backend/app/api/v1/questions.py`
- 修改：`backend/tests/unit/test_rag_service.py`
- 修改：`backend/scripts/evaluate_rag.py`
- 修改：`backend/tests/unit/test_evaluate_rag_script.py`
- 修改：`docs/阶段3执行进度.md`

- [ ] **Step 1：写不触发、成功改写、失败回退和原问题回答测试**

关键断言：Retriever 收到改写问题；`build_rag_prompt` 和最终回答仍收到用户原始问题；改写失败后 Retriever 收到原问题。

- [ ] **Step 2：按 `policy -> rewrite -> retrieve -> answer` 编排**
- [ ] **Step 3：增加 `--mode rewrite`，只在 multi_turn 分类计算提升**
- [ ] **Step 4：验证质量门**

多轮分类 Recall@5 相对未改写结果提升至少 15 个百分点；改写异常、超时、空响应回退测试全部通过。

- [ ] **Step 5：运行完整测试和 Ruff，更新看板并提交**

通过后将 3D 标为 `已完成`、3E Task 1 标为 `进行中`。

提交：`git commit -m "docs: 完成阶段3D问题改写验收"`


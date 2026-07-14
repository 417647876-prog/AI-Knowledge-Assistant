# 阶段 2C：文档管理闭环详细开发计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让登录用户能够持久化查看其可访问知识库中的文档，准确观察处理状态和失败原因，并安全地重处理或删除文档。

**Architecture:** 继续复用现有 FastAPI、SQLAlchemy Async、PostgreSQL/pgvector 和 Vue 3 单页工作台。后端补充可靠的“最新入库任务”时间字段、文档列表接口和删除接口；前端以 Pinia 作为文档列表的唯一状态源，在知识库切换时重新加载并恢复处理中任务的轮询。删除数据库数据依赖现有外键级联，原文件由同一业务操作显式清理。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2 Async、Alembic、PostgreSQL、pgvector、pytest、Vue 3、TypeScript、Pinia、Element Plus、Vitest、Vite。

## 1. 全局约束

- 只实现文档列表、处理状态、失败原因、重处理、删除和对应前端交互。
- 不实现流式回答、聊天历史、混合检索、Rerank、评估平台、独立 Worker 或任务取消。
- 不存在和无权访问的知识库或文档统一返回安全 404，不允许通过状态码判断资源是否属于其他用户。
- API 响应、错误信息和日志不得包含本地文件路径、数据库连接信息、令牌或密码。
- 所有新增后端行为先通过单元测试或 PostgreSQL 集成测试固定；前端行为先通过 API、Store 或组件测试固定。
- 保持小步修改，不重构无关模块，不删除阶段 1A—2B 的既有能力。
- PowerShell 下前端命令使用 `npm.cmd`，避免本机 `npm.ps1` 签名策略干扰。

## 2. 范围与完成定义

### 2.1 本阶段包含

1. 刷新浏览器后仍能看到数据库中已有的文档。
2. 文档状态能够区分：等待处理、正在解析、正在生成向量、可用、处理失败。
3. 失败文档显示安全、可理解的失败代码与原因。
4. 可用或失败文档可以重新处理；处理中不能重复提交。
5. 可用或失败文档可以删除；处理中暂不允许删除，避免与进程内后台任务争用原文件。
6. 删除成功后，文档、切片、向量、入库任务和本地原文件均不存在。
7. 删除后再次提问，回答不得引用已删除文档。

### 2.2 本阶段不包含

- 文档分页、搜索、排序筛选和批量删除。
- 处理中任务的强制取消。
- 对象存储、回收站、软删除和删除恢复。
- 跨数据库与文件系统的分布式事务。
- 服务端推送状态；状态更新继续使用现有轮询机制。

### 2.3 完成定义

- 后端静态检查、默认测试和启用 PostgreSQL 后的 2C 集成测试全部通过。
- 前端 API、Store、组件测试、类型检查和生产构建全部通过。
- 浏览器可以完整演示“上传 → 观察状态 → 刷新仍存在 → 重处理 → 提问并看到引用 → 删除 → 再提问无该引用”。
- README 中有中文运行和验收说明。

## 3. 关键设计决定

### 3.1 文档状态使用真实后端状态

当前后端实际写入 `pending`、`parsing`、`embedding`、`ready`、`failed`，前端类型中的 `running` 与后端不一致。本阶段统一为：

```text
pending   -> 等待处理
parsing   -> 正在解析
embedding -> 正在生成向量
ready     -> 可用
failed    -> 处理失败
```

`pending`、`parsing`、`embedding` 都属于“处理中”，这三种状态禁止重处理和删除。

### 3.2 最新任务不能按 UUID 大小判断

`ingestion_jobs.id` 是随机 UUID，`ORDER BY id DESC` 不能表示创建先后。新增 `ingestion_jobs.created_at`，所有“最新任务”查询统一使用：

```sql
ORDER BY created_at DESC, id DESC
```

`id` 只用于同一时间戳下的稳定排序，不承担时间语义。

### 3.3 删除一致性边界

PostgreSQL 事务无法与 Windows 本地文件系统组成真正的原子事务。本阶段采用以下明确边界：

1. 锁定授权范围内的文档记录。
2. 检查是否存在 `pending` 或 `running` 入库任务；存在则返回 409。
3. 在数据库事务中删除 `documents` 根记录，利用外键 `ON DELETE CASCADE` 清理 `document_chunks` 和 `ingestion_jobs`；向量随 `document_chunks.embedding` 一并删除。
4. 在提交数据库事务前删除本地原文件；文件本来就不存在时视为已清理。
5. 文件删除失败时回滚数据库事务，并返回不含路径的 `DOCUMENT_DELETE_FAILED`。
6. 极低概率的“文件已删除但数据库提交失败”无法在本阶段完全消除；后续阶段 4 可通过持久化任务和补偿清理解决。本阶段用集成测试保证正常路径和文件删除失败路径。

### 3.4 权限策略

- 普通用户只能列出、查询、重处理和删除自己知识库中的文档。
- 管理员继续遵循阶段 2B 的全局资源访问规则。
- 他人资源与随机 UUID 都返回既有 `DOCUMENT_NOT_FOUND` 或 `KNOWLEDGE_BASE_NOT_FOUND` 404。

## 4. API 契约

### 4.1 文档列表

```http
GET /api/v1/knowledge-bases/{knowledge_base_id}/documents
Authorization: Bearer <access_token>
```

成功响应：

```json
{
  "items": [
    {
      "document_id": "uuid",
      "job_id": "uuid",
      "file_name": "员工手册.docx",
      "status": "ready",
      "error_code": null,
      "error_message": null
    }
  ]
}
```

规则：

- 按 `documents.created_at DESC, documents.id DESC` 返回。
- 每份文档只返回 `created_at` 最新的一条入库任务。
- 不返回 `stored_file_name`、上传目录或任何内部路径。
- 知识库不存在或不可访问：404 `KNOWLEDGE_BASE_NOT_FOUND`。

### 4.2 查询单份文档

现有接口保持不变，但“最新任务”的排序改为 `created_at DESC, id DESC`：

```http
GET /api/v1/documents/{document_id}
```

### 4.3 重处理文档

复用现有接口：

```http
POST /api/v1/documents/{document_id}/reprocess
```

- 成功：202，返回新的 `job_id` 和 `pending` 状态。
- 存在 `pending` 或 `running` 任务：409 `DOCUMENT_PROCESSING`。
- 不存在或不可访问：404 `DOCUMENT_NOT_FOUND`。
- 新任务创建时间由数据库自动写入 `created_at`。

### 4.4 删除文档

```http
DELETE /api/v1/documents/{document_id}
```

- 成功：204，无响应体。
- 正在处理：409 `DOCUMENT_PROCESSING`。
- 不存在、已删除或不可访问：404 `DOCUMENT_NOT_FOUND`。
- 文件系统清理失败：500 `DOCUMENT_DELETE_FAILED`，消息固定为“文档删除失败，请稍后重试。”。

## 5. 文件改动地图

| 文件 | 责任 |
|---|---|
| `backend/migrations/versions/20260714_04_ingestion_job_created_at.py` | 增加入库任务创建时间并回填旧数据 |
| `backend/app/db/models/ingestion_job.py` | 映射 `created_at` 字段 |
| `backend/app/api/v1/documents.py` | 列表、查询、重处理、删除接口和响应模型 |
| `backend/app/knowledge/ingestion_service.py` | 处理和失败回写时可靠选择当前任务 |
| `backend/tests/integration/test_database_schema.py` | 固定迁移后的字段和索引 |
| `backend/tests/integration/test_document_management_api.py` | 固定列表、权限、并发与删除闭环 |
| `backend/tests/integration/test_document_reprocess_api.py` | 固定最新任务与重复提交行为 |
| `frontend/src/types/api.ts` | 对齐真实文档状态和列表响应 |
| `frontend/src/api/documents.ts` | 列表、重处理、删除请求 |
| `frontend/src/api/documents.spec.ts` | 固定 URL、HTTP method 和响应解析 |
| `frontend/src/stores/workspace.ts` | 持久化加载、轮询恢复、重处理和删除状态 |
| `frontend/src/stores/workspace.spec.ts` | 固定缓存隔离和过期响应保护 |
| `frontend/src/views/WorkspaceView.vue` | 知识库切换后加载文档并显示加载错误 |
| `frontend/src/views/WorkspaceView.spec.ts` | 固定首次加载、切换和错误恢复 |
| `frontend/src/components/DocumentTable.vue` | 状态、失败原因、重处理、删除确认 |
| `frontend/src/components/Documents.spec.ts` | 固定表格操作和按钮禁用规则 |
| `README.md`、`frontend/README.md` | 中文运行、操作和验收说明 |

---

## 6. 分步实施任务

### Task 1：为入库任务增加可靠创建时间

**Files:**
- Create: `backend/migrations/versions/20260714_04_ingestion_job_created_at.py`
- Modify: `backend/app/db/models/ingestion_job.py`
- Modify: `backend/tests/integration/test_database_schema.py`

**Interfaces:**
- Produces: `IngestionJob.created_at: datetime`，数据库非空且默认 `now()`。
- Consumes: 后续文档列表、单文档查询和入库服务的最新任务排序。

- [ ] **Step 1：先写数据库结构失败测试**

在 `test_database_schema.py` 增加断言：

```python
@pytest.mark.asyncio
async def test_ingestion_jobs_has_created_at_and_lookup_index() -> None:
    async with engine.connect() as connection:
        columns = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_columns("ingestion_jobs")
        )
        indexes = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_indexes("ingestion_jobs")
        )

    created_at = next(column for column in columns if column["name"] == "created_at")
    assert created_at["nullable"] is False
    assert any(
        index["name"] == "ix_ingestion_jobs_document_id_created_at"
        and index["column_names"] == ["document_id", "created_at"]
        for index in indexes
    )
```

- [ ] **Step 2：确认测试因字段不存在而失败**

Run:

```powershell
Set-Location backend
$env:RUN_DATABASE_TESTS='1'
uv run pytest tests/integration/test_database_schema.py::test_ingestion_jobs_has_created_at_and_lookup_index -q
```

Expected: FAIL，原因是 `created_at` 或组合索引尚不存在。

- [ ] **Step 3：新增迁移并回填旧任务**

迁移必须先允许空值、回填，再改为非空：

```python
def upgrade() -> None:
    op.add_column(
        "ingestion_jobs",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE ingestion_jobs
        SET created_at = COALESCE(started_at, finished_at, now())
        WHERE created_at IS NULL
        """
    )
    op.alter_column(
        "ingestion_jobs",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    op.create_index(
        "ix_ingestion_jobs_document_id_created_at",
        "ingestion_jobs",
        ["document_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ingestion_jobs_document_id_created_at",
        table_name="ingestion_jobs",
    )
    op.drop_column("ingestion_jobs", "created_at")
```

模型新增：

```python
created_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True), server_default=func.now(), nullable=False
)
```

- [ ] **Step 4：升级数据库并验证结构**

Run:

```powershell
uv run alembic upgrade head
uv run pytest tests/integration/test_database_schema.py -q
uv run ruff check app/db/models/ingestion_job.py migrations/versions/20260714_04_ingestion_job_created_at.py
```

Expected: 数据库结构测试和 ruff 全部通过。

- [ ] **Step 5：提交数据模型小步**

```powershell
git add backend/migrations/versions/20260714_04_ingestion_job_created_at.py backend/app/db/models/ingestion_job.py backend/tests/integration/test_database_schema.py
git commit -m "feat: 为文档任务增加创建时间"
```

### Task 2：实现持久化文档列表和可靠的最新任务查询

**Files:**
- Modify: `backend/app/api/v1/documents.py`
- Modify: `backend/app/knowledge/ingestion_service.py`
- Create: `backend/tests/integration/test_document_management_api.py`
- Modify: `backend/tests/integration/test_document_reprocess_api.py`

**Interfaces:**
- Produces: `GET /api/v1/knowledge-bases/{knowledge_base_id}/documents`。
- Produces: `DocumentTaskResponse` 与 `DocumentListResponse` Pydantic 响应模型。
- Consumes: Task 1 的 `IngestionJob.created_at`。

- [ ] **Step 1：写列表和权限失败测试**

测试至少覆盖以下名称和断言：

```python
async def test_list_documents_returns_only_target_knowledge_base_documents(): ...
async def test_list_documents_uses_latest_job_created_at_not_uuid_order(): ...
async def test_list_documents_returns_safe_404_for_other_users_knowledge_base(): ...
async def test_get_document_uses_latest_job_created_at(): ...
```

关键测试数据要显式让“较新的任务 UUID 更小”，证明代码没有依赖 UUID 排序：

```python
older_job = IngestionJob(
    id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
    document_id=document.id,
    created_at=datetime(2026, 7, 14, 8, 0, tzinfo=UTC),
)
newer_job = IngestionJob(
    id=UUID("00000000-0000-0000-0000-000000000001"),
    document_id=document.id,
    created_at=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
)
```

- [ ] **Step 2：确认端点和排序测试失败**

Run:

```powershell
$env:RUN_DATABASE_TESTS='1'
uv run pytest tests/integration/test_document_management_api.py tests/integration/test_document_reprocess_api.py -q
```

Expected: 列表端点未实现，旧的单文档查询仍可能选错任务。

- [ ] **Step 3：定义最小响应模型**

在 `documents.py` 内定义：

```python
class DocumentTaskResponse(BaseModel):
    document_id: UUID
    job_id: UUID
    file_name: str
    status: Literal["pending", "parsing", "embedding", "ready", "failed"]
    error_code: str | None
    error_message: str | None


class DocumentListResponse(BaseModel):
    items: list[DocumentTaskResponse]
```

上传、查询和重处理响应统一复用 `DocumentTaskResponse`；不加入内部存储文件名。

- [ ] **Step 4：实现最新任务查询和列表端点**

新增一个私有查询函数，所有端点复用：

```python
async def _latest_job(session: AsyncSession, document_id: UUID) -> IngestionJob | None:
    return await session.scalar(
        select(IngestionJob)
        .where(IngestionJob.document_id == document_id)
        .order_by(IngestionJob.created_at.desc(), IngestionJob.id.desc())
        .limit(1)
    )
```

列表端点先执行 `get_accessible_knowledge_base`，再按知识库过滤文档。第一版允许逐文档查询最新任务，先保证行为正确；本阶段文档量较小，不提前引入分页和复杂窗口查询。

`IngestionService._latest_job()` 仍需优先选择可执行的 `pending`、其次选择 `running`，但同一优先级内改用创建时间：

```python
.order_by(
    case(
        (IngestionJob.status == "pending", 0),
        (IngestionJob.status == "running", 1),
        else_=2,
    ),
    IngestionJob.created_at.desc(),
    IngestionJob.id.desc(),
)
```

这样新建的重处理任务会被后台流程选中，失败回写也不会误改旧任务。

- [ ] **Step 5：运行列表、重处理和权限回归**

Run:

```powershell
uv run pytest tests/integration/test_document_management_api.py tests/integration/test_document_reprocess_api.py tests/integration/test_resource_permissions.py -q
uv run ruff check app/api/v1/documents.py app/knowledge/ingestion_service.py tests/integration/test_document_management_api.py
```

Expected: 所有启用的测试通过，响应中不存在 `stored_file_name`。

- [ ] **Step 6：提交列表小步**

```powershell
git add backend/app/api/v1/documents.py backend/app/knowledge/ingestion_service.py backend/tests/integration/test_document_management_api.py backend/tests/integration/test_document_reprocess_api.py
git commit -m "feat: 添加持久化文档列表"
```

### Task 3：实现安全删除闭环

**Files:**
- Modify: `backend/app/api/v1/documents.py`
- Modify: `backend/tests/integration/test_document_management_api.py`
- Modify: `backend/tests/integration/test_question_api.py`

**Interfaces:**
- Produces: `DELETE /api/v1/documents/{document_id} -> 204`。
- Error: 409 `DOCUMENT_PROCESSING`、404 `DOCUMENT_NOT_FOUND`、500 `DOCUMENT_DELETE_FAILED`。
- Consumes: 既有 `ON DELETE CASCADE` 外键和 `get_accessible_document(..., for_update=True)`。

- [ ] **Step 1：写删除失败测试**

新增以下 PostgreSQL 集成测试：

```python
async def test_delete_document_removes_document_chunks_jobs_and_file(): ...
async def test_delete_document_rejects_active_ingestion_job(): ...
async def test_delete_document_returns_safe_404_for_other_user(): ...
async def test_delete_document_returns_safe_404_when_repeated(): ...
async def test_delete_document_rolls_back_database_when_file_unlink_fails(): ...
async def test_question_does_not_cite_deleted_document(): ...
```

正常删除测试必须在删除前创建：一条 `documents`、至少一条 `document_chunks`（含向量）、两条 `ingestion_jobs` 和一个真实临时文件。删除后逐项断言不存在。

- [ ] **Step 2：确认删除测试因端点不存在而失败**

Run:

```powershell
$env:RUN_DATABASE_TESTS='1'
uv run pytest tests/integration/test_document_management_api.py -k delete -q
```

Expected: DELETE 得到 405，或缺少预期业务行为。

- [ ] **Step 3：实现处理中保护**

在持有文档行锁后执行：

```python
active_job_id = await session.scalar(
    select(IngestionJob.id).where(
        IngestionJob.document_id == document_id,
        IngestionJob.status.in_(("pending", "running")),
    )
)
if active_job_id is not None:
    raise AppError(
        code="DOCUMENT_PROCESSING",
        message="文档正在处理中，请稍后再删除。",
        status_code=409,
    )
```

- [ ] **Step 4：实现数据库级联和文件清理**

删除逻辑必须遵守以下顺序：

```python
document = await get_accessible_document(
    session, current_user, document_id, for_update=True
)
upload_root = get_settings().upload_directory.resolve()
stored_file = (upload_root / document.stored_file_name).resolve()
if not stored_file.is_relative_to(upload_root):
    raise AppError(
        code="DOCUMENT_DELETE_FAILED",
        message="文档删除失败，请稍后重试。",
        status_code=500,
    )

try:
    await session.delete(document)
    await session.flush()
    stored_file.unlink(missing_ok=True)
    await session.commit()
except OSError as error:
    await session.rollback()
    raise AppError(
        code="DOCUMENT_DELETE_FAILED",
        message="文档删除失败，请稍后重试。",
        status_code=500,
    ) from error
```

代码和日志不得拼接 `stored_file` 的值到用户可见信息中。

- [ ] **Step 5：固定“删除后不再引用”行为**

在 `test_question_api.py` 中先让目标文档产生可检索切片并确认问题响应引用该 `document_id`，删除文档后再次提问，断言所有 `citations[].document_id` 都不等于已删除 ID，且数据库中该文档切片数量为 0。

- [ ] **Step 6：运行删除闭环回归**

Run:

```powershell
uv run pytest tests/integration/test_document_management_api.py tests/integration/test_question_api.py -q
uv run ruff check app tests/integration/test_document_management_api.py tests/integration/test_question_api.py
```

Expected: 删除、权限、文件失败回滚和问答引用测试全部通过。

- [ ] **Step 7：提交删除小步**

```powershell
git add backend/app/api/v1/documents.py backend/tests/integration/test_document_management_api.py backend/tests/integration/test_question_api.py
git commit -m "feat: 添加文档安全删除闭环"
```

### Task 4：对齐前端类型和 API 封装

**Files:**
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/api/documents.ts`
- Modify: `frontend/src/api/documents.spec.ts`

**Interfaces:**
- Produces: `listDocuments(knowledgeBaseId)`、`reprocessDocument(documentId)`、`deleteDocument(documentId)`。
- Produces: 精确的 `DocumentStatus` 和 `DocumentListResponse`。

- [ ] **Step 1：先写 API 失败测试**

```typescript
it('请求文档列表、重处理和删除端点', async () => {
  await listDocuments('kb-1')
  await reprocessDocument('doc-1')
  await deleteDocument('doc-1')

  expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
    ['/api/v1/knowledge-bases/kb-1/documents', undefined],
    ['/api/v1/documents/doc-1/reprocess', 'POST'],
    ['/api/v1/documents/doc-1', 'DELETE'],
  ])
})
```

- [ ] **Step 2：确认 API 函数尚不存在**

Run:

```powershell
Set-Location frontend
npm.cmd test -- --run src/api/documents.spec.ts
```

Expected: FAIL，指出新增函数不存在或请求不匹配。

- [ ] **Step 3：对齐类型**

```typescript
export type DocumentStatus =
  | 'pending'
  | 'parsing'
  | 'embedding'
  | 'ready'
  | 'failed'

export interface DocumentTask {
  document_id: string
  job_id: string
  file_name: string
  status: DocumentStatus
  error_code: string | null
  error_message: string | null
}

export interface DocumentListResponse {
  items: DocumentTask[]
}
```

- [ ] **Step 4：实现三个 API 函数**

```typescript
export async function listDocuments(knowledgeBaseId: string) {
  const response = await apiRequest<DocumentListResponse>(
    `/api/v1/knowledge-bases/${knowledgeBaseId}/documents`,
  )
  return response.items
}

export const reprocessDocument = (id: string) =>
  apiRequest<DocumentTask>(`/api/v1/documents/${id}/reprocess`, { method: 'POST' })

export const deleteDocument = (id: string) =>
  apiRequest<void>(`/api/v1/documents/${id}`, { method: 'DELETE' })
```

- [ ] **Step 5：运行 API 测试和类型检查**

```powershell
npm.cmd test -- --run src/api/documents.spec.ts
npm.cmd run type-check
```

Expected: API 测试和 TypeScript 检查通过。

- [ ] **Step 6：提交前端 API 小步**

```powershell
git add frontend/src/types/api.ts frontend/src/api/documents.ts frontend/src/api/documents.spec.ts
git commit -m "feat: 添加文档管理前端接口"
```

### Task 5：实现 Pinia 文档持久化状态

**Files:**
- Modify: `frontend/src/stores/workspace.ts`
- Modify: `frontend/src/stores/workspace.spec.ts`

**Interfaces:**
- Produces: `loadDocuments()`、`reprocessDocument(documentId)`、`deleteDocument(documentId)`。
- Maintains: `documents[knowledgeBaseId]`、`loadingDocuments`、内部 `pollingDocumentIds`。
- Consumes: Task 4 的三个 API 函数和现有 `pollDocumentStatus`。

- [ ] **Step 1：写 Store 失败测试**

测试名称与边界：

```typescript
it('加载当前知识库的历史文档')
it('切换知识库后旧列表响应不能覆盖新知识库')
it('为重新加载出的处理中任务恢复轮询')
it('同一文档不会启动两个状态轮询')
it('重处理后立即替换任务编号并轮询到终态')
it('删除成功后才从列表移除文档')
it('删除失败时保留原文档行')
it('reset 后忽略旧用户的文档响应')
```

- [ ] **Step 2：确认 Store 动作不存在而失败**

Run:

```powershell
npm.cmd test -- --run src/stores/workspace.spec.ts
```

Expected: FAIL，原因是 Store 尚未提供对应动作或过期响应仍写入状态。

- [ ] **Step 3：实现列表加载和过期响应保护**

`loadDocuments()` 必须同时记录 `generation` 和发起请求时的 `knowledgeBaseId`：

```typescript
async function loadDocuments() {
  const operationGeneration = generation
  const knowledgeBaseId = activeKnowledgeBaseId.value
  if (!knowledgeBaseId) return
  const loaded = await listDocuments(knowledgeBaseId)
  if (
    operationGeneration !== generation
    || activeKnowledgeBaseId.value !== knowledgeBaseId
  ) return
  documents.value[knowledgeBaseId] = loaded
  for (const document of loaded.filter(isProcessingDocument)) {
    void trackDocument(knowledgeBaseId, document)
  }
}
```

- [ ] **Step 4：抽取统一轮询入口**

上传、重新处理和页面恢复都调用 `trackDocument`；内部用 `Set<string>` 防止重复轮询。终态只接受 `ready` 或 `failed`，轮询异常不能伪装成后端处理失败，交由调用界面显示请求错误。

- [ ] **Step 5：实现重处理和删除动作**

- 重处理：请求成功后保留原 `file_name`，用新 `job_id` 替换当前行，再进入统一轮询。
- 删除：仅在 DELETE 成功后过滤当前知识库数组；请求失败时原行不变。
- 所有写入都检查 `generation` 和原知识库 ID。

- [ ] **Step 6：运行 Store 测试和类型检查**

```powershell
npm.cmd test -- --run src/stores/workspace.spec.ts
npm.cmd run type-check
```

Expected: Store 测试和类型检查全部通过。

- [ ] **Step 7：提交 Store 小步**

```powershell
git add frontend/src/stores/workspace.ts frontend/src/stores/workspace.spec.ts
git commit -m "feat: 持久化加载并管理文档状态"
```

### Task 6：实现文档管理界面

**Files:**
- Modify: `frontend/src/views/WorkspaceView.vue`
- Modify: `frontend/src/views/WorkspaceView.spec.ts`
- Modify: `frontend/src/components/DocumentTable.vue`
- Modify: `frontend/src/components/Documents.spec.ts`

**Interfaces:**
- Consumes: Task 5 的 Store 状态和动作。
- Produces: 状态展示、失败原因、刷新、重处理、删除确认和操作反馈。

- [ ] **Step 1：写 WorkspaceView 失败测试**

覆盖：首次选中知识库后加载文档、切换知识库后重新加载、文档加载失败时显示格式化错误、点击“重新加载”后恢复。

- [ ] **Step 2：写 DocumentTable 失败测试**

覆盖：

```typescript
it('显示五种后端文档状态的中文标签')
it('显示失败代码和安全失败原因')
it('处理中禁用重处理和删除')
it('点击重处理后调用 Store 并显示成功反馈')
it('确认删除后调用 Store')
it('取消删除时不调用 Store')
it('操作失败时保留文档行并显示格式化错误')
```

- [ ] **Step 3：确认组件测试失败**

Run:

```powershell
npm.cmd test -- --run src/views/WorkspaceView.spec.ts src/components/Documents.spec.ts
```

Expected: FAIL，缺少加载监听、状态标签和操作按钮。

- [ ] **Step 4：实现知识库切换加载**

`WorkspaceView.vue` 使用 `watch` 监听 `activeKnowledgeBaseId`，在值非空时调用 `store.loadDocuments()`；用独立的 `documentLoadError` 展示文档错误，不能覆盖知识库初始加载错误。

- [ ] **Step 5：实现表格操作**

状态映射固定为第 3.1 节。操作列规则：

- `pending`、`parsing`、`embedding`：两个按钮禁用。
- `ready`、`failed`：允许重处理和删除。
- 删除前调用 `ElMessageBox.confirm`，正文包含用户可见文件名，不包含内部路径。
- 行操作期间只禁用当前行，其他文档仍可操作。
- 空状态文案改为“当前知识库暂无文档”，移除“当前会话”表述。

- [ ] **Step 6：运行组件、全量前端测试和构建**

```powershell
npm.cmd test -- --run src/views/WorkspaceView.spec.ts src/components/Documents.spec.ts
npm.cmd test -- --run
npm.cmd run type-check
npm.cmd run build
```

Expected: 组件测试、前端全量测试、类型检查和构建全部通过。

- [ ] **Step 7：提交界面小步**

```powershell
git add frontend/src/views/WorkspaceView.vue frontend/src/views/WorkspaceView.spec.ts frontend/src/components/DocumentTable.vue frontend/src/components/Documents.spec.ts
git commit -m "feat: 添加文档管理操作界面"
```

### Task 7：补充中文文档和端到端验收

**Files:**
- Modify: `README.md`
- Modify: `frontend/README.md`
- Create: `docs/阶段2C验证与演示.md`

**Interfaces:**
- Produces: 可重复执行的中文运行、验证和面试演示步骤。

- [ ] **Step 1：更新中文说明**

README 必须说明：

- 选择知识库后会从数据库加载历史文档。
- 五种文档状态及含义。
- 重处理与删除的可用条件。
- 删除不可恢复，并会清理该文档的检索内容。
- PostgreSQL 集成测试必须显式设置 `RUN_DATABASE_TESTS=1`。

- [ ] **Step 2：编写阶段 2C 验收文档**

`docs/阶段2C验证与演示.md` 至少包含：环境准备、数据库迁移、前后端启动、自动测试、浏览器演示、权限 404 验证、删除后问答验证和已知边界。

- [ ] **Step 3：运行后端完整验证**

```powershell
Set-Location backend
uv run ruff check app tests migrations
uv run pytest -q
$env:RUN_DATABASE_TESTS='1'
uv run pytest tests/integration/test_database_schema.py tests/integration/test_document_management_api.py tests/integration/test_document_reprocess_api.py tests/integration/test_question_api.py tests/integration/test_resource_permissions.py -q
```

Expected: ruff 无诊断；默认测试全部通过；指定 PostgreSQL 集成测试全部通过。

- [ ] **Step 4：运行前端完整验证**

```powershell
Set-Location ..\frontend
npm.cmd test -- --run
npm.cmd run type-check
npm.cmd run build
```

Expected: Vitest、TypeScript 和 Vite 构建全部通过。

- [ ] **Step 5：执行浏览器验收**

1. 登录普通用户并创建知识库。
2. 上传一份能回答明确事实问题的文档。
3. 观察状态从等待处理经过解析、向量化变为可用。
4. 刷新浏览器，确认文档仍在列表中。
5. 点击重处理，确认任务编号和状态发生变化并最终回到可用。
6. 提问并记录该文档引用。
7. 确认删除，确认文档行消失。
8. 再次提问，确认回答不再包含已删除文档引用。
9. 使用另一普通用户验证无法看到或操作该知识库文档。

- [ ] **Step 6：检查差异和提交文档**

```powershell
Set-Location ..
git diff --check
git status --short
git add README.md frontend/README.md docs/阶段2C验证与演示.md docs/superpowers/plans/2026-07-14-stage-2c-document-management.md
git commit -m "docs: 添加阶段 2C 验证与演示说明"
```

Expected: 差异检查退出码为 0，提交只包含阶段 2C 文档。

## 7. 测试矩阵

| 层级 | 场景 | 预期 |
|---|---|---|
| 数据库 | `created_at` 回填与非空约束 | 旧任务有时间，新任务自动写入 |
| 数据库 | 文档级联删除 | 文档、切片、向量、任务全部消失 |
| API | 列出目标知识库文档 | 只返回授权知识库数据 |
| API | 最新任务 UUID 反序 | 仍按 `created_at` 选择新任务 |
| API | 他人知识库或文档 | 安全 404 |
| API | 重复重处理 | 409 `DOCUMENT_PROCESSING` |
| API | 删除处理中任务 | 409 `DOCUMENT_PROCESSING` |
| API | 文件删除失败 | 500 且数据库回滚，不泄露路径 |
| RAG | 删除后再次提问 | 不再出现已删除文档引用 |
| API 客户端 | GET/POST/DELETE 路径 | URL 和 method 正确 |
| Pinia | 切换知识库时旧响应晚到 | 不污染当前知识库 |
| Pinia | 重新打开页面发现处理中任务 | 自动恢复轮询且不重复 |
| 组件 | 五种状态 | 显示正确中文标签 |
| 组件 | 删除确认与取消 | 确认才请求，取消不请求 |
| 浏览器 | 刷新页面 | 历史文档仍可见 |
| 浏览器 | 完整删除演示 | 文档消失且问答不再引用 |

## 8. 风险与回退

- **迁移风险：** 旧任务没有原始创建时间。回填优先使用 `started_at`、其次 `finished_at`、最后迁移时间；不会伪造比现有字段更精确的信息。
- **后台任务竞争：** 本阶段不支持取消进程内任务，因此处理中禁止删除和重复重处理。
- **文件系统差异：** Windows 可能因文件占用导致删除失败；API 回滚数据库并返回安全错误，用户稍后重试。
- **前端过期请求：** 继续复用 `generation`，并增加知识库 ID 校验，防止切换用户或知识库后的旧请求覆盖新状态。
- **回退方式：** 代码回退后执行 Alembic downgrade 只删除 `ingestion_jobs.created_at` 和组合索引，不删除文档业务数据。

## 9. 最终检查清单

- [ ] 路线图中的文档列表、状态、失败原因、重处理、删除均有实现任务。
- [ ] 删除正常路径覆盖文档、切片、向量、任务和本地文件。
- [ ] 404 权限隔离、409 并发保护和 500 安全错误均有测试。
- [ ] 文档状态前后端命名完全一致。
- [ ] 所有最新任务查询都使用 `created_at DESC, id DESC`。
- [ ] 前端刷新后可以加载历史文档并恢复处理中任务轮询。
- [ ] 删除后问答不再引用已删除内容。
- [ ] 没有把阶段 2D、3A、3B 或 4 的能力混入 2C。
- [ ] 自动测试、生产构建和浏览器验收都有明确命令与预期。

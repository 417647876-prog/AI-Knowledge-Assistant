# 阶段 2A：Vue 前端知识库工作台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个直接调用现有 FastAPI 的 Vue 单页工作台，让用户能创建知识库、上传文档、查看处理状态、提问并查看真实引用。

**Architecture:** 前端位于独立的 `frontend/` 目录，资源化 API 模块隔离 HTTP，Pinia 维护页面会话状态，Vue 组件只负责交互与展示。Vite 将 `/api`、`/health`、`/ready` 代理到本地 FastAPI；文档状态由可注入时钟的轮询函数驱动。

**Tech Stack:** Node 24.15、npm 11.12、Vue 3.5、Vite 8、TypeScript 7、Element Plus 2.14、Pinia 3、Vitest 4、Vue Test Utils 2、jsdom 29

## Global Constraints

- 前端直接调用现有 FastAPI，不增加 BFF、Node 服务或后端接口。
- 本阶段不实现登录、JWT、权限隔离、流式回答、聊天历史和文档删除。
- 后端没有文档列表接口；只展示当前浏览器会话上传过的文档。
- 支持 `.txt`、`.md`、`.pdf`、`.docx`、`.xlsx`，以后端校验为最终结果。
- 文档状态每 2 秒轮询，在 `ready`、`failed` 或 2 分钟超时时停止。
- PowerShell 统一使用 `npm.cmd`，避免执行策略拦截 `npm.ps1`。
- 新增行为测试先行，每个任务独立提交。

---

## File Map

```text
frontend/
├── package.json
├── vite.config.ts
├── index.html
└── src/
    ├── main.ts
    ├── App.vue
    ├── styles/main.css
    ├── types/api.ts
    ├── api/client.ts
    ├── api/knowledgeBases.ts
    ├── api/documents.ts
    ├── api/questions.ts
    ├── stores/workspace.ts
    ├── components/KnowledgeBaseSidebar.vue
    ├── components/DocumentUpload.vue
    ├── components/DocumentTable.vue
    ├── components/QuestionPanel.vue
    └── test/setup.ts
```

### Task 1: 前端脚手架与测试基线

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/src/main.ts`
- Create: `frontend/src/App.vue`
- Create: `frontend/src/App.spec.ts`
- Create: `frontend/src/test/setup.ts`
- Create: `frontend/src/styles/main.css`

**Interfaces:**
- Consumes: Node 24.15、FastAPI `127.0.0.1:8000`。
- Produces: `npm.cmd run test`、`npm.cmd run type-check`、`npm.cmd run build`。

- [ ] **Step 1: 创建 Vue TypeScript 工程并安装依赖**

```powershell
npm.cmd create vite@8.1.0 frontend -- --template vue-ts
Set-Location frontend
npm.cmd install
npm.cmd install element-plus@2.14.3 pinia@3.0.4
npm.cmd install -D vitest@4.1.10 @vue/test-utils@2.4.11 jsdom@29.1.1 vue-tsc@3.3.7 @testing-library/vue@8.1.0 @vitest/coverage-v8@4.1.10
```

Expected: `frontend/package-lock.json` exists; install exits 0.

- [ ] **Step 2: 写根组件失败测试**

Create `frontend/src/App.spec.ts`:

```ts
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import App from './App.vue'

describe('App', () => {
  it('renders the knowledge assistant shell', () => {
    const wrapper = mount(App)
    expect(wrapper.get('h1').text()).toBe('AI 知识库助手')
    expect(wrapper.text()).toContain('请选择或创建知识库')
  })
})
```

- [ ] **Step 3: 配置 Vitest 和代理，运行失败测试**

Replace `frontend/vite.config.ts`:

```ts
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: { proxy: {
    '/api': 'http://127.0.0.1:8000',
    '/health': 'http://127.0.0.1:8000',
    '/ready': 'http://127.0.0.1:8000',
  } },
  test: { environment: 'jsdom', setupFiles: ['./src/test/setup.ts'], clearMocks: true },
})
```

Create `frontend/src/test/setup.ts`:

```ts
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/vue'
afterEach(() => cleanup())
```

Run: `npm.cmd run test -- --run src/App.spec.ts`

Expected: FAIL because generated `App.vue` lacks required text.

- [ ] **Step 4: 写最小应用入口**

Replace `frontend/src/App.vue`:

```vue
<template>
  <main class="app-shell">
    <header class="app-header"><h1>AI 知识库助手</h1></header>
    <section class="workspace-empty">请选择或创建知识库</section>
  </main>
</template>
```

Replace `frontend/src/main.ts`:

```ts
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import './styles/main.css'

createApp(App).use(createPinia()).use(ElementPlus).mount('#app')
```

Create `frontend/src/styles/main.css`:

```css
:root { font-family: Inter, "Microsoft YaHei", sans-serif; color: #18212f; background: #f4f6f8; }
* { box-sizing: border-box; }
body { margin: 0; min-width: 320px; min-height: 100vh; }
.app-header { padding: 20px 28px; color: #fff; background: #17324d; }
.app-header h1 { margin: 0; font-size: 22px; }
.workspace-empty { padding: 64px; text-align: center; color: #667085; }
```

- [ ] **Step 5: 配置 scripts 并验证**

Set `package.json` scripts to:

```json
{
  "dev": "vite",
  "build": "vue-tsc -b && vite build",
  "type-check": "vue-tsc -b",
  "test": "vitest",
  "test:coverage": "vitest run --coverage"
}
```

Run:

```powershell
npm.cmd run test -- --run src/App.spec.ts
npm.cmd run type-check
npm.cmd run build
```

Expected: 1 test passes; type-check and build exit 0.

- [ ] **Step 6: Commit**

```powershell
git add frontend
git commit -m "feat: 搭建 Vue 前端测试基线"
```

---

### Task 2: FastAPI 类型契约与统一 HTTP 错误

**Files:**
- Create: `frontend/src/types/api.ts`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/api/client.spec.ts`

**Interfaces:**
- Consumes: FastAPI JSON 与 `{ error: { code, message, request_id } }`。
- Produces: `ApiError`、`apiRequest<T>(path, init?)`、`formatApiError(error)`。

- [ ] **Step 1: 写失败测试**

Create `frontend/src/api/client.spec.ts`:

```ts
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiRequest, formatApiError } from './client'

afterEach(() => vi.unstubAllGlobals())

describe('apiRequest', () => {
  it('returns JSON on success', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"status":"ready"}', {
      status: 200, headers: { 'Content-Type': 'application/json' },
    })))
    await expect(apiRequest('/ready')).resolves.toEqual({ status: 'ready' })
  })

  it('maps FastAPI errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: { code: 'FILE_TOO_LARGE', message: '文件超过 20 MB 限制。', request_id: 'req-1' },
    }), { status: 413, headers: { 'Content-Type': 'application/json' } })))
    await expect(apiRequest('/upload')).rejects.toEqual(
      new ApiError(413, 'FILE_TOO_LARGE', '文件超过 20 MB 限制。', 'req-1'),
    )
  })

  it('maps network errors safely', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))
    await expect(apiRequest('/ready')).rejects.toMatchObject({
      code: 'NETWORK_ERROR', message: '服务暂不可用，请稍后重试。',
    })
  })

  it('formats code and request id for the UI', () => {
    const error = new ApiError(413, 'FILE_TOO_LARGE', '文件过大。', 'req-1')
    expect(formatApiError(error)).toBe('文件过大。 [FILE_TOO_LARGE] 请求标识：req-1')
  })
})
```

- [ ] **Step 2: 运行测试确认模块缺失**

Run: `npm.cmd run test -- --run src/api/client.spec.ts`

Expected: FAIL with `Cannot find module './client'`.

- [ ] **Step 3: 定义 API 类型**

Create `frontend/src/types/api.ts`:

```ts
export type DocumentStatus = 'pending' | 'running' | 'ready' | 'failed'
export interface KnowledgeBase { id: string; name: string; description: string | null }
export interface DocumentTask {
  document_id: string; job_id: string; status: DocumentStatus
  error_code: string | null; error_message: string | null; file_name?: string
}
export interface Citation {
  citation_id: number; document_id: string; file_name: string; content: string
  relevance_score: number; page_number: number | null; sheet_name: string | null
  row_start: number | null; section_title: string | null
}
export interface QuestionResponse {
  answer: string; citations: Citation[]; retrieved_chunk_count: number; request_id: string
}
export interface ApiErrorEnvelope {
  error?: { code?: string; message?: string; request_id?: string }
}
```

- [ ] **Step 4: 实现统一请求函数**

Create `frontend/src/api/client.ts`:

```ts
import type { ApiErrorEnvelope } from '../types/api'

export class ApiError extends Error {
  constructor(
    public readonly status: number, public readonly code: string,
    message: string, public readonly requestId?: string,
  ) { super(message); this.name = 'ApiError' }
}

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try { response = await fetch(path, init) }
  catch { throw new ApiError(0, 'NETWORK_ERROR', '服务暂不可用，请稍后重试。') }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as ApiErrorEnvelope
    throw new ApiError(
      response.status, payload.error?.code ?? 'HTTP_ERROR',
      payload.error?.message ?? '请求失败，请稍后重试。', payload.error?.request_id,
    )
  }
  return await response.json() as T
}

export function formatApiError(error: unknown) {
  if (!(error instanceof ApiError)) return error instanceof Error ? error.message : '请求失败。'
  const code = error.code ? ` [${error.code}]` : ''
  const requestId = error.requestId ? ` 请求标识：${error.requestId}` : ''
  return `${error.message}${code}${requestId}`
}
```

- [ ] **Step 5: 验证并提交**

```powershell
npm.cmd run test -- --run src/api/client.spec.ts
npm.cmd run type-check
git add frontend/src/api frontend/src/types
git commit -m "feat: 添加前端 API 契约与统一错误"
```

Expected: 4 tests pass; type-check exits 0.

---

### Task 3: 知识库 API 与工作区 Store

**Files:**
- Create: `frontend/src/api/knowledgeBases.ts`
- Create: `frontend/src/stores/workspace.ts`
- Create: `frontend/src/stores/workspace.spec.ts`

**Interfaces:**
- Consumes: `apiRequest<T>`、`KnowledgeBase`。
- Produces: `listKnowledgeBases()`、`createKnowledgeBase(input)`、`useWorkspaceStore()`。

- [ ] **Step 1: 写 Store 失败测试**

Create `frontend/src/stores/workspace.spec.ts`:

```ts
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/knowledgeBases', () => ({
  listKnowledgeBases: vi.fn(), createKnowledgeBase: vi.fn(),
}))

import { createKnowledgeBase, listKnowledgeBases } from '../api/knowledgeBases'
import { useWorkspaceStore } from './workspace'

describe('workspace knowledge bases', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('loads and selects the first knowledge base', async () => {
    vi.mocked(listKnowledgeBases).mockResolvedValue([
      { id: 'kb-1', name: '人事制度', description: null },
    ])
    const store = useWorkspaceStore()
    await store.loadKnowledgeBases()
    expect(store.activeKnowledgeBaseId).toBe('kb-1')
  })

  it('adds and selects a newly created knowledge base', async () => {
    vi.mocked(createKnowledgeBase).mockResolvedValue({
      id: 'kb-2', name: '研发规范', description: '研发资料',
    })
    const store = useWorkspaceStore()
    await store.createKnowledgeBase({ name: '研发规范', description: '研发资料' })
    expect(store.knowledgeBases).toHaveLength(1)
    expect(store.activeKnowledgeBaseId).toBe('kb-2')
  })
})
```

- [ ] **Step 2: 运行测试确认模块缺失**

Run: `npm.cmd run test -- --run src/stores/workspace.spec.ts`

Expected: FAIL because API and store modules do not exist.

- [ ] **Step 3: 实现知识库 API**

Create `frontend/src/api/knowledgeBases.ts`:

```ts
import type { KnowledgeBase } from '../types/api'
import { apiRequest } from './client'

export interface CreateKnowledgeBaseInput { name: string; description: string | null }
export const listKnowledgeBases = () => apiRequest<KnowledgeBase[]>('/api/v1/knowledge-bases')
export const createKnowledgeBase = (input: CreateKnowledgeBaseInput) =>
  apiRequest<KnowledgeBase>('/api/v1/knowledge-bases', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input),
  })
```

- [ ] **Step 4: 实现 Store 的知识库状态**

Create `frontend/src/stores/workspace.ts`:

```ts
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { createKnowledgeBase as createRequest, listKnowledgeBases } from '../api/knowledgeBases'
import type { CreateKnowledgeBaseInput } from '../api/knowledgeBases'
import type { DocumentTask, KnowledgeBase, QuestionResponse } from '../types/api'

export const useWorkspaceStore = defineStore('workspace', () => {
  const knowledgeBases = ref<KnowledgeBase[]>([])
  const activeKnowledgeBaseId = ref<string | null>(null)
  const documents = ref<Record<string, DocumentTask[]>>({})
  const answer = ref<QuestionResponse | null>(null)
  const loadingKnowledgeBases = ref(false)
  const activeKnowledgeBase = computed(() =>
    knowledgeBases.value.find((item) => item.id === activeKnowledgeBaseId.value) ?? null)
  const activeDocuments = computed(() => activeKnowledgeBaseId.value
    ? documents.value[activeKnowledgeBaseId.value] ?? [] : [])

  async function loadKnowledgeBases() {
    loadingKnowledgeBases.value = true
    try {
      knowledgeBases.value = await listKnowledgeBases()
      if (!activeKnowledgeBaseId.value && knowledgeBases.value.length)
        activeKnowledgeBaseId.value = knowledgeBases.value[0].id
    } finally { loadingKnowledgeBases.value = false }
  }

  async function createKnowledgeBase(input: CreateKnowledgeBaseInput) {
    const created = await createRequest(input)
    knowledgeBases.value.push(created)
    activeKnowledgeBaseId.value = created.id
    return created
  }

  function selectKnowledgeBase(id: string) {
    activeKnowledgeBaseId.value = id
    answer.value = null
  }

  return {
    knowledgeBases, activeKnowledgeBaseId, documents, answer, loadingKnowledgeBases,
    activeKnowledgeBase, activeDocuments, loadKnowledgeBases, createKnowledgeBase,
    selectKnowledgeBase,
  }
})
```

- [ ] **Step 5: 验证并提交**

```powershell
npm.cmd run test -- --run src/stores/workspace.spec.ts
npm.cmd run type-check
git add frontend/src/api/knowledgeBases.ts frontend/src/stores
git commit -m "feat: 添加知识库前端状态"
```

Expected: 2 tests pass; type-check exits 0.

---

### Task 4: 文档上传与状态轮询

**Files:**
- Create: `frontend/src/api/documents.ts`
- Create: `frontend/src/api/documents.spec.ts`
- Modify: `frontend/src/stores/workspace.ts`
- Modify: `frontend/src/stores/workspace.spec.ts`

**Interfaces:**
- Consumes: `apiRequest<T>`、`DocumentTask`、当前知识库 ID。
- Produces: `uploadDocument()`、`getDocument()`、`pollDocumentStatus()`、`uploadAndTrackDocument(file)`。

- [ ] **Step 1: 写轮询失败测试**

Create `frontend/src/api/documents.spec.ts`:

```ts
import { describe, expect, it, vi } from 'vitest'
import { pollDocumentStatus } from './documents'
import type { DocumentTask } from '../types/api'

const task = (status: DocumentTask['status']): DocumentTask => ({
  document_id: 'doc-1', job_id: 'job-1', status, error_code: null, error_message: null,
})

describe('pollDocumentStatus', () => {
  it('stops when ready', async () => {
    const request = vi.fn().mockResolvedValueOnce(task('running')).mockResolvedValueOnce(task('ready'))
    const result = await pollDocumentStatus('doc-1', {
      request, sleep: () => Promise.resolve(), intervalMs: 0, timeoutMs: 100,
    })
    expect(result.status).toBe('ready')
    expect(request).toHaveBeenCalledTimes(2)
  })

  it('returns failed state', async () => {
    const request = vi.fn().mockResolvedValue(task('failed'))
    await expect(pollDocumentStatus('doc-1', {
      request, sleep: () => Promise.resolve(), intervalMs: 0, timeoutMs: 100,
    })).resolves.toMatchObject({ status: 'failed' })
  })

  it('throws after timeout', async () => {
    const request = vi.fn().mockResolvedValue(task('running'))
    let now = 0
    await expect(pollDocumentStatus('doc-1', {
      request, sleep: async () => { now = 101 }, now: () => now,
      intervalMs: 0, timeoutMs: 100,
    })).rejects.toMatchObject({ code: 'DOCUMENT_POLL_TIMEOUT' })
  })
})
```

- [ ] **Step 2: 运行测试确认模块缺失**

Run: `npm.cmd run test -- --run src/api/documents.spec.ts`

Expected: FAIL because `documents.ts` does not exist.

- [ ] **Step 3: 实现上传、查询与轮询**

Create `frontend/src/api/documents.ts`:

```ts
import type { DocumentTask } from '../types/api'
import { ApiError, apiRequest } from './client'

export function uploadDocument(knowledgeBaseId: string, file: File) {
  const body = new FormData(); body.append('file', file)
  return apiRequest<DocumentTask>(`/api/v1/knowledge-bases/${knowledgeBaseId}/documents`, {
    method: 'POST', body,
  })
}
export const getDocument = (id: string) => apiRequest<DocumentTask>(`/api/v1/documents/${id}`)

interface PollOptions {
  intervalMs?: number; timeoutMs?: number; request?: typeof getDocument
  sleep?: (milliseconds: number) => Promise<void>; now?: () => number
}

export async function pollDocumentStatus(id: string, options: PollOptions = {}) {
  const intervalMs = options.intervalMs ?? 2_000
  const timeoutMs = options.timeoutMs ?? 120_000
  const request = options.request ?? getDocument
  const sleep = options.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)))
  const now = options.now ?? Date.now
  const deadline = now() + timeoutMs
  while (true) {
    const document = await request(id)
    if (document.status === 'ready' || document.status === 'failed') return document
    if (now() >= deadline)
      throw new ApiError(0, 'DOCUMENT_POLL_TIMEOUT', '文档处理时间较长，请稍后刷新状态。')
    await sleep(intervalMs)
  }
}
```

- [ ] **Step 4: 写 Store 上传测试并实现 action**

Extend `workspace.spec.ts` with:

```ts
vi.mock('../api/documents', () => ({
  uploadDocument: vi.fn(), pollDocumentStatus: vi.fn(),
}))
import { pollDocumentStatus, uploadDocument } from '../api/documents'

it('tracks an uploaded document until ready', async () => {
  const pending = {
    document_id: 'doc-1', job_id: 'job-1', status: 'pending' as const,
    error_code: null, error_message: null,
  }
  vi.mocked(uploadDocument).mockResolvedValue(pending)
  vi.mocked(pollDocumentStatus).mockResolvedValue({ ...pending, status: 'ready' })
  const store = useWorkspaceStore()
  store.activeKnowledgeBaseId = 'kb-1'

  await store.uploadAndTrackDocument(new File(['制度'], '制度.txt'))

  expect(store.activeDocuments[0]).toMatchObject({
    status: 'ready', file_name: '制度.txt',
  })
})
```

Add to `workspace.ts`:

```ts
import { pollDocumentStatus, uploadDocument } from '../api/documents'

async function uploadAndTrackDocument(file: File) {
  const id = activeKnowledgeBaseId.value
  if (!id) throw new Error('请先选择知识库。')
  const pending = { ...await uploadDocument(id, file), file_name: file.name }
  documents.value[id] = [pending, ...(documents.value[id] ?? [])]
  const finished = { ...await pollDocumentStatus(pending.document_id), file_name: file.name }
  documents.value[id] = documents.value[id].map((item) =>
    item.document_id === finished.document_id ? finished : item)
  return finished
}
```

Expose `uploadAndTrackDocument` from the store.

- [ ] **Step 5: 验证并提交**

```powershell
npm.cmd run test -- --run src/api/documents.spec.ts src/stores/workspace.spec.ts
npm.cmd run type-check
git add frontend/src/api/documents* frontend/src/stores
git commit -m "feat: 添加文档上传与状态轮询"
```

Expected: API and store tests pass; type-check exits 0.

---

### Task 5: 知识库侧边栏与文档区域

**Files:**
- Create: `frontend/src/components/KnowledgeBaseSidebar.vue`
- Create: `frontend/src/components/KnowledgeBaseSidebar.spec.ts`
- Create: `frontend/src/components/DocumentUpload.vue`
- Create: `frontend/src/components/DocumentTable.vue`
- Create: `frontend/src/components/Documents.spec.ts`

**Interfaces:**
- Consumes: `useWorkspaceStore()`、Element Plus。
- Produces: 创建/选择知识库、上传文件、文档状态展示。

- [ ] **Step 1: 写知识库侧边栏失败测试**

The test activates a Pinia instance, seeds `knowledgeBases` with `人事制度`, mounts the component with Element Plus, then executes:

```ts
expect(wrapper.text()).toContain('人事制度')
await wrapper.get('[data-test="create-knowledge-base"]').trigger('click')
expect(wrapper.get('[data-test="knowledge-base-dialog"]').isVisible()).toBe(true)
```

- [ ] **Step 2: 写文档区域失败测试**

The test seeds `activeKnowledgeBaseId = 'kb-1'` and one running document, mounts both document components, then executes:

```ts
expect(wrapper.get('input[type="file"]').attributes('accept')).toBe('.txt,.md,.pdf,.docx,.xlsx')
expect(wrapper.text()).toContain('处理中')
expect(wrapper.text()).toContain('制度.txt')
```

Run:

```powershell
npm.cmd run test -- --run src/components/KnowledgeBaseSidebar.spec.ts src/components/Documents.spec.ts
```

Expected: FAIL because the three components do not exist.

- [ ] **Step 3: 实现知识库侧边栏**

`KnowledgeBaseSidebar.vue` imports `formatApiError` from `../api/client`, uses `store.knowledgeBases`, `store.selectKnowledgeBase`, an Element Plus dialog and this submit function:

```ts
async function submit() {
  if (!form.name.trim()) return ElMessage.warning('请输入知识库名称。')
  submitting.value = true
  try {
    await store.createKnowledgeBase({
      name: form.name.trim(), description: form.description.trim() || null,
    })
    dialogVisible.value = false
    form.name = ''; form.description = ''
  } catch (error) {
    ElMessage.error(formatApiError(error))
  } finally { submitting.value = false }
}
```

The template must use `data-test="create-knowledge-base"` on the new button, `data-test="knowledge-base-dialog"` on the dialog, and an `el-menu-item` per knowledge base.

- [ ] **Step 4: 实现上传与文档表格**

`DocumentUpload.vue` contains a native file input with `accept=".txt,.md,.pdf,.docx,.xlsx"`, blocks concurrent upload, calls `store.uploadAndTrackDocument(file)`, and uses `ElMessage.success` or `ElMessage.error(formatApiError(error))` so the error code and request ID remain visible.

`DocumentTable.vue` renders `store.activeDocuments` and this exact status mapping:

```ts
const labels = {
  pending: '等待处理', running: '处理中', ready: '可用', failed: '处理失败',
} as const
```

The table columns are file name, status, and `error_message`; an empty table shows `当前会话还没有上传文档`.

- [ ] **Step 5: 验证并提交**

```powershell
npm.cmd run test -- --run src/components/KnowledgeBaseSidebar.spec.ts src/components/Documents.spec.ts
npm.cmd run type-check
git add frontend/src/components
git commit -m "feat: 添加知识库与文档工作区"
```

Expected: component tests pass; type-check exits 0.

---

### Task 6: 问答 API 与引用展示

**Files:**
- Create: `frontend/src/api/questions.ts`
- Create: `frontend/src/components/QuestionPanel.vue`
- Create: `frontend/src/components/QuestionPanel.spec.ts`
- Modify: `frontend/src/stores/workspace.ts`
- Modify: `frontend/src/stores/workspace.spec.ts`

**Interfaces:**
- Consumes: `QuestionResponse`、当前知识库 ID、`apiRequest<T>`。
- Produces: `askQuestion()`、`submitQuestion()`、答案和结构化引用 UI。

- [ ] **Step 1: 写回答与引用失败测试**

Seed the store with one answer and citation, mount the component, then assert:

```ts
expect(wrapper.text()).toContain('员工有 5 天年假')
expect(wrapper.text()).toContain('年假制度.txt')
expect(wrapper.text()).toContain('第 1 页')
expect(wrapper.text()).toContain('相关度 0.91')
```

Set the textarea to spaces, click submit, and assert `store.submitQuestion` was not called and `ElMessage.warning` received `请输入问题。`.

Run: `npm.cmd run test -- --run src/components/QuestionPanel.spec.ts`

Expected: FAIL because `QuestionPanel.vue` does not exist.

- [ ] **Step 2: 实现问答 API**

Create `frontend/src/api/questions.ts`:

```ts
import type { QuestionResponse } from '../types/api'
import { apiRequest } from './client'

export const askQuestion = (knowledgeBaseId: string, question: string, topK = 5) =>
  apiRequest<QuestionResponse>(`/api/v1/knowledge-bases/${knowledgeBaseId}/questions`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, top_k: topK }),
  })
```

- [ ] **Step 3: 实现 Store 问答 action**

Add `asking = ref(false)` and:

```ts
async function submitQuestion(question: string) {
  if (!activeKnowledgeBaseId.value) throw new Error('请先选择知识库。')
  asking.value = true
  try {
    answer.value = await askQuestion(activeKnowledgeBaseId.value, question.trim(), 5)
    return answer.value
  } finally { asking.value = false }
}
```

Expose `asking` and `submitQuestion`, and extend `workspace.spec.ts` with:

```ts
vi.mock('../api/questions', () => ({ askQuestion: vi.fn() }))
import { askQuestion } from '../api/questions'

it('stores a completed answer and clears loading state', async () => {
  const result = {
    answer: '员工有 5 天年假。[1]', citations: [],
    retrieved_chunk_count: 1, request_id: 'req-1',
  }
  vi.mocked(askQuestion).mockResolvedValue(result)
  const store = useWorkspaceStore()
  store.activeKnowledgeBaseId = 'kb-1'

  await store.submitQuestion('有多少天年假？')

  expect(store.answer).toEqual(result)
  expect(store.asking).toBe(false)
})
```

- [ ] **Step 4: 实现问答面板**

`QuestionPanel.vue` imports `formatApiError` from `../api/client` and renders:

- an `el-input` textarea with `maxlength="2000"`;
- a button disabled when no knowledge base is active and loading from `store.asking`;
- answer, `retrieved_chunk_count`, and `request_id`;
- citation cards showing file name, content, `relevance_score.toFixed(2)`;
- `第 N 页`、工作表、行号 only when corresponding metadata is non-null;
- no citation container when `citations.length === 0`.
- submit errors through `ElMessage.error(formatApiError(error))`.

- [ ] **Step 5: 验证并提交**

```powershell
npm.cmd run test -- --run src/components/QuestionPanel.spec.ts src/stores/workspace.spec.ts
npm.cmd run type-check
git add frontend/src/api/questions.ts frontend/src/components/QuestionPanel* frontend/src/stores
git commit -m "feat: 添加知识库问答与引用展示"
```

Expected: question and store tests pass; type-check exits 0.

---

### Task 7: 组装单页工作台与响应式视觉

**Files:**
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/App.spec.ts`
- Modify: `frontend/src/styles/main.css`

**Interfaces:**
- Consumes: 四个组件、`useWorkspaceStore()`。
- Produces: 桌面两栏、小于 900px 时纵向的工作台。

- [ ] **Step 1: 写根组件集成失败测试**

Mock `loadKnowledgeBases`, mount with Pinia and assert:

```ts
expect(wrapper.findComponent(KnowledgeBaseSidebar).exists()).toBe(true)
expect(wrapper.findComponent(DocumentUpload).exists()).toBe(true)
expect(wrapper.findComponent(DocumentTable).exists()).toBe(true)
expect(wrapper.findComponent(QuestionPanel).exists()).toBe(true)
expect(loadKnowledgeBases).toHaveBeenCalledOnce()
```

Run: `npm.cmd run test -- --run src/App.spec.ts`

Expected: FAIL because `App.vue` still contains only the initial shell.

- [ ] **Step 2: 组装根组件**

`App.vue` imports the four components, calls `store.loadKnowledgeBases()` in `onMounted`, always renders the sidebar, and renders upload/table/question only when `store.activeKnowledgeBase` exists. Otherwise it renders `请选择或创建知识库`.

- [ ] **Step 3: 完成视觉和响应式规则**

Append to `main.css`:

```css
.workspace-layout { display: grid; grid-template-columns: 280px minmax(0, 1fr); min-height: calc(100vh - 68px); }
.knowledge-sidebar { padding: 20px; background: #fff; border-right: 1px solid #e5e7eb; }
.sidebar-title { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.workspace-main { display: grid; gap: 20px; align-content: start; padding: 24px; }
.workspace-card { padding: 20px; border: 1px solid #e5e7eb; border-radius: 14px; background: #fff; box-shadow: 0 8px 24px rgb(15 23 42 / 5%); }
.citation-list { display: grid; gap: 12px; margin-top: 16px; }
@media (max-width: 900px) {
  .workspace-layout { grid-template-columns: 1fr; }
  .knowledge-sidebar { border-right: 0; border-bottom: 1px solid #e5e7eb; }
  .workspace-main { padding: 16px; }
}
```

- [ ] **Step 4: 全量前端验证并提交**

```powershell
npm.cmd run test -- --run
npm.cmd run type-check
npm.cmd run build
git add frontend/src
git commit -m "feat: 组装知识库单页工作台"
```

Expected: all frontend tests pass; `dist/` is generated; type-check and build exit 0.

---

### Task 8: 使用文档与真实 API 验收

**Files:**
- Modify: `.gitignore`
- Modify: `README.md`
- Create: `frontend/README.md`

**Interfaces:**
- Consumes: Docker PostgreSQL、FastAPI、第一阶段测试资料。
- Produces: 启动说明、人工验收结果、干净工作树。

- [ ] **Step 1: 忽略前端生成目录**

Append to `.gitignore`:

```gitignore
frontend/node_modules/
frontend/dist/
frontend/coverage/
```

- [ ] **Step 2: 写中文启动文档**

`frontend/README.md` includes:

```powershell
# Terminal 1
docker compose -f deploy/docker-compose.yml up -d
Set-Location backend
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --loop app.core.event_loop:new_event_loop

# Terminal 2
Set-Location frontend
npm.cmd install
npm.cmd run dev
```

Document frontend URL `http://127.0.0.1:5173` and Swagger URL `http://127.0.0.1:8000/docs`.

- [ ] **Step 3: 更新根 README**

Add a “阶段 2A 前端” section containing stack, two-terminal startup, supported actions, current-session document limitation, and a link to `frontend/README.md`.

- [ ] **Step 4: 执行真实 API 验收**

1. Create `阶段2A验收`.
2. Upload `backend/tests/fixtures/documents/01-年假制度.txt`.
3. Observe `pending/running` changing to `ready`.
4. Ask `员工入职满一年后有多少天年假？`.
5. Confirm citation file is `01-年假制度.txt`.
6. Upload `09-不支持格式.csv` and confirm `UNSUPPORTED_FILE_TYPE` appears.

Expected: all six checks pass without opening Swagger.

- [ ] **Step 5: 最终自动化验证**

```powershell
Set-Location frontend
npm.cmd run test -- --run
npm.cmd run type-check
npm.cmd run build
Set-Location ..\backend
$env:RUN_DATABASE_TESTS = "1"
uv run pytest -q
uv run ruff check app tests migrations scripts
uv run ruff format --check app tests migrations scripts
```

Expected: frontend tests pass; type-check/build exit 0; backend 45 tests pass; Ruff checks pass.

- [ ] **Step 6: Commit**

```powershell
git add .gitignore README.md frontend/README.md
git commit -m "docs: 添加阶段 2A 前端使用说明"
```

---

## 阶段 2A 完成检查

- [ ] 用户可以在浏览器创建并选择知识库。
- [ ] 用户可以上传五种支持格式并观察状态。
- [ ] 文档轮询在完成、失败或超时时停止。
- [ ] 用户可以提问并看到真实引用元数据。
- [ ] 常见 FastAPI 错误显示中文信息、错误码和 request ID。
- [ ] 页面在桌面和窄屏下可操作。
- [ ] 前端测试、类型检查、构建和后端回归测试通过。
- [ ] 工作树没有 `node_modules`、`dist`、coverage 或 20MB 边界文件。

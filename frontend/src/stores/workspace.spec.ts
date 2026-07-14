import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/knowledgeBases', () => ({
  listKnowledgeBases: vi.fn(), createKnowledgeBase: vi.fn(),
}))
vi.mock('../api/documents', () => ({
  deleteDocument: vi.fn(), listDocuments: vi.fn(), pollDocumentStatus: vi.fn(),
  reprocessDocument: vi.fn(), uploadDocument: vi.fn(),
}))
vi.mock('../api/questions', () => ({ askQuestion: vi.fn() }))

import { createKnowledgeBase, listKnowledgeBases } from '../api/knowledgeBases'
import {
  deleteDocument, listDocuments, pollDocumentStatus, reprocessDocument, uploadDocument,
} from '../api/documents'
import { askQuestion } from '../api/questions'
import { ApiError } from '../api/client'
import type { DocumentTask } from '../types/api'
import { useWorkspaceStore } from './workspace'

describe('workspace knowledge bases', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('resets authentication-scoped workspace state', () => {
    const store = useWorkspaceStore()
    store.knowledgeBases = [{
      id: 'kb-1', name: '人事制度', description: null,
      owner_id: 'u-1', owner_username: 'alice',
    }]
    store.activeKnowledgeBaseId = 'kb-1'
    store.documents = { 'kb-1': [{
      document_id: 'doc-1', job_id: 'job-1', file_name: '员工手册.txt', status: 'ready',
      error_code: null, error_message: null,
    }] }
    store.answer = {
      answer: '答案', citations: [], retrieved_chunk_count: 1, request_id: 'req-1',
    }
    store.asking = true

    store.reset()

    expect(store.knowledgeBases).toEqual([])
    expect(store.activeKnowledgeBaseId).toBeNull()
    expect(store.documents).toEqual({})
    expect(store.answer).toBeNull()
    expect(store.asking).toBe(false)
  })

  it('ignores an old-session knowledge base response after reset', async () => {
    let resolveKnowledgeBases!: (items: Array<{
      id: string; name: string; description: null; owner_id: string; owner_username: string
    }>) => void
    vi.mocked(listKnowledgeBases).mockReturnValue(new Promise((resolve) => {
      resolveKnowledgeBases = resolve
    }))
    const store = useWorkspaceStore()

    const loading = store.loadKnowledgeBases()
    store.reset()
    resolveKnowledgeBases([{
      id: 'old-kb', name: '旧用户数据', description: null,
      owner_id: 'old-user', owner_username: 'old-user',
    }])
    await loading

    expect(store.knowledgeBases).toEqual([])
    expect(store.activeKnowledgeBaseId).toBeNull()
  })

  it('loads and selects the first knowledge base', async () => {
    vi.mocked(listKnowledgeBases).mockResolvedValue([
      {
        id: 'kb-1', name: '人事制度', description: null,
        owner_id: 'u-1', owner_username: 'alice',
      },
    ])
    const store = useWorkspaceStore()
    await store.loadKnowledgeBases()
    expect(store.activeKnowledgeBaseId).toBe('kb-1')
  })

  it('加载当前知识库的历史文档', async () => {
    const documents = [{
      document_id: 'doc-1', job_id: 'job-1', file_name: '员工手册.txt', status: 'ready' as const,
      error_code: null, error_message: null,
    }]
    vi.mocked(listDocuments).mockResolvedValue(documents)
    const store = useWorkspaceStore()
    store.activeKnowledgeBaseId = 'kb-1'

    await store.loadDocuments()

    expect(listDocuments).toHaveBeenCalledWith('kb-1')
    expect(store.activeDocuments).toEqual(documents)
  })

  it('切换知识库后忽略旧请求返回的文档列表', async () => {
    let resolveDocuments!: (items: Array<{
      document_id: string; job_id: string; file_name: string; status: 'ready'
      error_code: null; error_message: null
    }>) => void
    vi.mocked(listDocuments).mockReturnValue(new Promise((resolve) => { resolveDocuments = resolve }))
    const store = useWorkspaceStore()
    store.activeKnowledgeBaseId = 'kb-a'

    const loading = store.loadDocuments()
    store.selectKnowledgeBase('kb-b')
    resolveDocuments([{
      document_id: 'doc-a', job_id: 'job-a', file_name: '旧知识库.txt', status: 'ready',
      error_code: null, error_message: null,
    }])
    await loading

    expect(store.documents).toEqual({})
  })

  it('同一知识库的旧列表响应不能覆盖较新的结果', async () => {
    let resolveOld!: (items: DocumentTask[]) => void
    const oldRequest = new Promise<DocumentTask[]>((resolve) => { resolveOld = resolve })
    const newer = [{
      document_id: 'doc-new', job_id: 'job-new', file_name: '新列表.txt', status: 'ready' as const,
      error_code: null, error_message: null,
    }]
    vi.mocked(listDocuments)
      .mockReturnValueOnce(oldRequest)
      .mockResolvedValueOnce(newer)
    const store = useWorkspaceStore()
    store.activeKnowledgeBaseId = 'kb-1'

    const loadingOld = store.loadDocuments()
    await store.loadDocuments()
    resolveOld([{
      document_id: 'doc-old', job_id: 'job-old', file_name: '旧列表.txt', status: 'ready',
      error_code: null, error_message: null,
    }])
    await loadingOld

    expect(store.activeDocuments).toEqual(newer)
  })

  it('恢复处理中历史文档的轮询，且不重复发起同一文档轮询', async () => {
    const parsing = {
      document_id: 'doc-1', job_id: 'job-1', file_name: '员工手册.txt', status: 'parsing' as const,
      error_code: null, error_message: null,
    }
    let resolvePoll!: (document: DocumentTask) => void
    vi.mocked(listDocuments).mockResolvedValue([parsing])
    vi.mocked(pollDocumentStatus).mockReturnValue(new Promise((resolve) => { resolvePoll = resolve }))
    const store = useWorkspaceStore()
    store.activeKnowledgeBaseId = 'kb-1'

    await store.loadDocuments()
    await store.loadDocuments()
    expect(pollDocumentStatus).toHaveBeenCalledTimes(1)

    resolvePoll({ ...parsing, status: 'ready' })
    await Promise.resolve()
    expect(store.activeDocuments[0]).toMatchObject({ status: 'ready' })
  })

  it('重置后的新轮询不会被旧轮询结束时解除去重保护', async () => {
    const parsing = {
      document_id: 'doc-1', job_id: 'job-1', file_name: '员工手册.txt', status: 'parsing' as const,
      error_code: null, error_message: null,
    }
    let resolveOldPoll!: (document: DocumentTask) => void
    vi.mocked(listDocuments).mockResolvedValue([parsing])
    vi.mocked(pollDocumentStatus)
      .mockReturnValueOnce(new Promise((resolve) => { resolveOldPoll = resolve }))
      .mockReturnValueOnce(new Promise(() => {}))
    const store = useWorkspaceStore()
    store.activeKnowledgeBaseId = 'kb-1'
    await store.loadDocuments()

    store.reset()
    store.activeKnowledgeBaseId = 'kb-1'
    await store.loadDocuments()
    resolveOldPoll({ ...parsing, status: 'ready' })
    await Promise.resolve()
    await store.loadDocuments()

    expect(pollDocumentStatus).toHaveBeenCalledTimes(2)
  })

  it('重处理后更新对应文档行', async () => {
    const pending = {
      document_id: 'doc-1', job_id: 'job-2', file_name: '员工手册.txt', status: 'pending' as const,
      error_code: null, error_message: null,
    }
    vi.mocked(reprocessDocument).mockResolvedValue(pending)
    vi.mocked(pollDocumentStatus).mockResolvedValue({ ...pending, status: 'ready' })
    const store = useWorkspaceStore()
    store.activeKnowledgeBaseId = 'kb-1'
    store.documents = { 'kb-1': [{ ...pending, job_id: 'job-1', status: 'failed' }] }

    await store.reprocessDocument('doc-1')

    expect(reprocessDocument).toHaveBeenCalledWith('doc-1')
    expect(store.activeDocuments[0]).toMatchObject({ job_id: 'job-2', status: 'ready' })
  })

  it('删除成功后移除对应文档行', async () => {
    vi.mocked(deleteDocument).mockResolvedValue(undefined)
    const store = useWorkspaceStore()
    store.activeKnowledgeBaseId = 'kb-1'
    store.documents = { 'kb-1': [{
      document_id: 'doc-1', job_id: 'job-1', file_name: '员工手册.txt', status: 'ready', error_code: null, error_message: null,
    }] }

    await store.deleteDocument('doc-1')

    expect(deleteDocument).toHaveBeenCalledWith('doc-1')
    expect(store.activeDocuments).toEqual([])
  })

  it('删除失败时保留原文档行', async () => {
    const error = new ApiError(500, 'DOCUMENT_DELETE_FAILED', '文档删除失败。')
    vi.mocked(deleteDocument).mockRejectedValue(error)
    const store = useWorkspaceStore()
    store.activeKnowledgeBaseId = 'kb-1'
    store.documents = { 'kb-1': [{
      document_id: 'doc-1', job_id: 'job-1', file_name: '员工手册.txt', status: 'ready',
      error_code: null, error_message: null,
    }] }

    await expect(store.deleteDocument('doc-1')).rejects.toBe(error)
    expect(store.activeDocuments).toHaveLength(1)
  })

  it('adds and selects a newly created knowledge base', async () => {
    vi.mocked(createKnowledgeBase).mockResolvedValue({
      id: 'kb-2', name: '研发规范', description: '研发资料',
      owner_id: 'u-1', owner_username: 'alice',
    })
    const store = useWorkspaceStore()
    await store.createKnowledgeBase({ name: '研发规范', description: '研发资料' })
    expect(store.knowledgeBases).toHaveLength(1)
    expect(store.activeKnowledgeBaseId).toBe('kb-2')
  })

  it('新建并切换知识库时清空旧答案', async () => {
    vi.mocked(createKnowledgeBase).mockResolvedValue({
      id: 'kb-2', name: '研发规范', description: null,
      owner_id: 'u-1', owner_username: 'alice',
    })
    const store = useWorkspaceStore()
    store.answer = {
      answer: '旧知识库答案', citations: [],
      retrieved_chunk_count: 1, request_id: 'req-old',
    }

    await store.createKnowledgeBase({ name: '研发规范', description: null })

    expect(store.activeKnowledgeBaseId).toBe('kb-2')
    expect(store.answer).toBeNull()
  })

  it('tracks an uploaded document until ready', async () => {
    const pending = {
      document_id: 'doc-1', job_id: 'job-1', file_name: '制度.txt', status: 'pending' as const,
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

  it.each([
    [
      '轮询超时',
      new ApiError(0, 'DOCUMENT_POLL_TIMEOUT', '文档处理时间较长，请稍后刷新状态。'),
    ],
    ['状态查询失败', new Error('状态查询失败')],
  ])('%s 时保留后端处理状态并向调用方报告错误', async (_name, error) => {
    const pending = {
      document_id: 'doc-failed', job_id: 'job-failed', file_name: '制度.txt', status: 'pending' as const,
      error_code: null, error_message: null,
    }
    vi.mocked(uploadDocument).mockResolvedValue(pending)
    vi.mocked(pollDocumentStatus).mockRejectedValue(error)
    const store = useWorkspaceStore()
    store.activeKnowledgeBaseId = 'kb-1'

    await expect(store.uploadAndTrackDocument(new File(['制度'], '制度.txt'))).rejects.toBe(error)

    expect(store.activeDocuments[0]).toEqual({
      document_id: 'doc-failed',
      job_id: 'job-failed',
      file_name: '制度.txt',
      status: 'pending',
      error_code: null,
      error_message: null,
    })
  })

  it('保存问答结果并在完成后清除加载状态', async () => {
    const result = {
      answer: '员工有 5 天年假。[1]', citations: [],
      retrieved_chunk_count: 1, request_id: 'req-1',
    }
    vi.mocked(askQuestion).mockResolvedValue(result)
    const store = useWorkspaceStore()
    store.activeKnowledgeBaseId = 'kb-1'

    await store.submitQuestion('  有多少天年假？  ')

    expect(askQuestion).toHaveBeenCalledWith('kb-1', '有多少天年假？', 5)
    expect(store.answer).toEqual(result)
    expect(store.asking).toBe(false)
  })

  it('问答失败时也会清除加载状态', async () => {
    vi.mocked(askQuestion).mockRejectedValue(new Error('服务异常'))
    const store = useWorkspaceStore()
    store.activeKnowledgeBaseId = 'kb-1'

    await expect(store.submitQuestion('问题')).rejects.toThrow('服务异常')

    expect(store.asking).toBe(false)
  })

  it('切换知识库后忽略旧知识库尚未返回的问答结果', async () => {
    let resolveQuestion!: (result: {
      answer: string; citations: []; retrieved_chunk_count: number; request_id: string
    }) => void
    vi.mocked(askQuestion).mockReturnValue(new Promise((resolve) => {
      resolveQuestion = resolve
    }))
    const store = useWorkspaceStore()
    store.activeKnowledgeBaseId = 'kb-a'

    const pending = store.submitQuestion('A 库问题')
    store.selectKnowledgeBase('kb-b')
    resolveQuestion({
      answer: 'A 库答案', citations: [],
      retrieved_chunk_count: 1, request_id: 'req-a',
    })
    await pending

    expect(store.activeKnowledgeBaseId).toBe('kb-b')
    expect(store.answer).toBeNull()
  })
})

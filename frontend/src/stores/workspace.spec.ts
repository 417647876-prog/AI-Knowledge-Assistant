import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/knowledgeBases', () => ({
  listKnowledgeBases: vi.fn(), createKnowledgeBase: vi.fn(),
}))
vi.mock('../api/documents', () => ({
  uploadDocument: vi.fn(), pollDocumentStatus: vi.fn(),
}))
vi.mock('../api/questions', () => ({ askQuestion: vi.fn() }))

import { createKnowledgeBase, listKnowledgeBases } from '../api/knowledgeBases'
import { pollDocumentStatus, uploadDocument } from '../api/documents'
import { askQuestion } from '../api/questions'
import { ApiError } from '../api/client'
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
      document_id: 'doc-1', job_id: 'job-1', status: 'ready',
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

  it.each([
    [
      '轮询超时',
      new ApiError(0, 'DOCUMENT_POLL_TIMEOUT', '文档处理时间较长，请稍后刷新状态。'),
      'DOCUMENT_POLL_TIMEOUT',
      '文档处理时间较长，请稍后刷新状态。',
    ],
    ['状态查询失败', new Error('状态查询失败'), 'DOCUMENT_POLL_FAILED', '状态查询失败'],
  ])('%s 时将文档行更新为明确失败状态', async (_name, error, code, message) => {
    const pending = {
      document_id: 'doc-failed', job_id: 'job-failed', status: 'pending' as const,
      error_code: null, error_message: null,
    }
    vi.mocked(uploadDocument).mockResolvedValue(pending)
    vi.mocked(pollDocumentStatus).mockRejectedValue(error)
    const store = useWorkspaceStore()
    store.activeKnowledgeBaseId = 'kb-1'

    await expect(store.uploadAndTrackDocument(new File(['制度'], '制度.txt'))).rejects.toBe(error)

    expect(store.activeDocuments[0]).toMatchObject({
      document_id: 'doc-failed',
      job_id: 'job-failed',
      file_name: '制度.txt',
      status: 'failed',
      error_code: code,
      error_message: message,
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

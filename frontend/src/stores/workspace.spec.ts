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
})

import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/knowledgeBases', () => ({
  listKnowledgeBases: vi.fn(), createKnowledgeBase: vi.fn(),
}))
vi.mock('../api/documents', () => ({
  uploadDocument: vi.fn(), pollDocumentStatus: vi.fn(),
}))

import { createKnowledgeBase, listKnowledgeBases } from '../api/knowledgeBases'
import { pollDocumentStatus, uploadDocument } from '../api/documents'
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
})

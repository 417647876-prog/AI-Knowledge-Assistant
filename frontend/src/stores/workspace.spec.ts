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

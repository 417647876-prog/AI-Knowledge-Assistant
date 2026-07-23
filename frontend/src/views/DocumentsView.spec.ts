import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '../stores/auth'
import { useConversationsStore } from '../stores/conversations'
import { useWorkspaceStore } from '../stores/workspace'
import DocumentsView from './DocumentsView.vue'

async function setup() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<main>首页</main>' } },
      { path: '/knowledge-bases/:knowledgeBaseId', component: DocumentsView },
      { path: '/knowledge-bases/:knowledgeBaseId/conversations', component: { template: '<main>会话</main>' } },
    ],
  })
  await router.push('/knowledge-bases/kb-1')
  await router.isReady()
  const auth = useAuthStore()
  const workspace = useWorkspaceStore()
  const conversations = useConversationsStore()
  auth.user = { id: 'u-1', username: 'alice', role: 'user', is_active: true }
  workspace.knowledgeBases = [
    { id: 'kb-1', name: '知识库一', description: null, owner_id: 'u-1', owner_username: 'alice' },
    { id: 'kb-2', name: '知识库二', description: null, owner_id: 'u-1', owner_username: 'alice' },
  ]
  workspace.activeKnowledgeBaseId = 'kb-1'
  vi.spyOn(workspace, 'loadDocuments').mockResolvedValue()
  return { pinia, router, workspace, conversations }
}

describe('DocumentsView 会话入口', () => {
  it('只提供专用会话页入口，不在文档页重复激活或渲染会话', async () => {
    const { pinia, router, conversations } = await setup()
    const activate = vi.spyOn(conversations, 'activate').mockResolvedValue()
    const wrapper = mount(DocumentsView, {
      global: { plugins: [pinia, router, ElementPlus] },
    })
    await flushPromises()

    expect(activate).not.toHaveBeenCalled()
    expect(wrapper.find('.question-panel').exists()).toBe(false)
    await wrapper.get('[data-test="open-conversations"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.fullPath).toBe('/knowledge-bases/kb-1/conversations')
  })
})

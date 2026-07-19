import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
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

describe('DocumentsView 会话激活', () => {
  it('会话加载失败后可显式强制重试', async () => {
    const { pinia, router, conversations } = await setup()
    const activate = vi.spyOn(conversations, 'activate')
      .mockRejectedValueOnce(new ApiError(503, 'SERVICE_UNAVAILABLE', '会话暂不可用'))
      .mockResolvedValueOnce()
    const wrapper = mount(DocumentsView, {
      global: { plugins: [pinia, router, ElementPlus] },
    })
    await flushPromises()

    expect(wrapper.get('[data-test="conversation-load-error"]').text())
      .toContain('会话暂不可用 [SERVICE_UNAVAILABLE]')
    await wrapper.get('[data-test="reload-conversations"]').trigger('click')
    await flushPromises()

    expect(activate).toHaveBeenLastCalledWith('u-1', 'kb-1', true)
    expect(wrapper.find('[data-test="conversation-load-error"]').exists()).toBe(false)
  })

  it('旧知识库迟到的失败不会覆盖新知识库成功状态', async () => {
    const { pinia, router, workspace, conversations } = await setup()
    let rejectOld!: (reason: unknown) => void
    const activate = vi.spyOn(conversations, 'activate')
      .mockReturnValueOnce(new Promise((_resolve, reject) => { rejectOld = reject }))
      .mockResolvedValueOnce()
    const wrapper = mount(DocumentsView, {
      global: { plugins: [pinia, router, ElementPlus] },
    })
    await flushPromises()

    workspace.activeKnowledgeBaseId = 'kb-2'
    await flushPromises()
    rejectOld(new ApiError(503, 'OLD_FAILURE', '旧请求失败'))
    await flushPromises()

    expect(activate).toHaveBeenLastCalledWith('u-1', 'kb-2', false)
    expect(wrapper.find('[data-test="conversation-load-error"]').exists()).toBe(false)
  })
})

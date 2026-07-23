import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus, { ElMessageBox } from 'element-plus'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import QuestionPanel from '../components/QuestionPanel.vue'
import { useAuthStore } from '../stores/auth'
import { useConversationsStore } from '../stores/conversations'
import { useWorkspaceStore } from '../stores/workspace'
import ConversationsView from './ConversationsView.vue'

async function setup() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<main>首页</main>' } },
      { path: '/knowledge-bases/:knowledgeBaseId/conversations', component: ConversationsView },
      { path: '/knowledge-bases/:knowledgeBaseId/documents', component: { template: '<main>文档</main>' } },
    ],
  })
  await router.push('/knowledge-bases/kb-1/conversations')
  await router.isReady()

  const auth = useAuthStore()
  const workspace = useWorkspaceStore()
  const conversations = useConversationsStore()
  auth.user = { id: 'u-1', username: 'alice', role: 'user', is_active: true }
  workspace.knowledgeBases = [{
    id: 'kb-1', name: '研发规范', description: null,
    owner_id: 'u-1', owner_username: 'alice',
  }]
  conversations.conversations = [
    {
      id: 'conversation-1', knowledge_base_id: 'kb-1', title: '请假制度',
      created_at: '2026-07-20T08:00:00Z', updated_at: '2026-07-20T08:10:00Z',
    },
    {
      id: 'conversation-2', knowledge_base_id: 'kb-1', title: '报销规则',
      created_at: '2026-07-20T07:00:00Z', updated_at: '2026-07-20T07:10:00Z',
    },
  ]
  conversations.activeConversationId = 'conversation-1'
  conversations.activeUserId = 'u-1'
  conversations.activeKnowledgeBaseId = 'kb-1'
  const activate = vi.spyOn(conversations, 'activate').mockResolvedValue()
  return { pinia, router, workspace, conversations, activate }
}

describe('ConversationsView', () => {
  afterEach(() => {
    document.body.replaceChildren()
    vi.restoreAllMocks()
  })

  it('展示手机会话列表并通过 Store 切换服务端会话', async () => {
    const { pinia, router, conversations } = await setup()
    const openConversation = vi.spyOn(conversations, 'openConversation').mockResolvedValue()
    const wrapper = mount(ConversationsView, {
      global: { plugins: [pinia, router, ElementPlus] },
    })
    await flushPromises()

    expect(wrapper.get('[data-test="conversation-history"]').text()).toContain('请假制度')
    expect(wrapper.get('[data-test="conversation-history"]').text()).toContain('报销规则')
    expect(wrapper.findComponent(QuestionPanel).exists()).toBe(true)
    expect(wrapper.get('[data-test="conversation-item-conversation-1"]').attributes('aria-current'))
      .toBe('true')

    await wrapper.get('[data-test="conversation-item-conversation-2"]').trigger('click')
    await flushPromises()

    expect(openConversation).toHaveBeenCalledWith('conversation-2')
  })

  it('切换失败只展示格式化错误，不保留旧回答区域', async () => {
    const { pinia, router, conversations } = await setup()
    vi.spyOn(conversations, 'openConversation').mockImplementation(async () => {
      conversations.messages = []
      throw new ApiError(404, 'CONVERSATION_NOT_FOUND', '会话不存在。')
    })
    const wrapper = mount(ConversationsView, {
      global: { plugins: [pinia, router, ElementPlus] },
    })
    await flushPromises()

    await wrapper.get('[data-test="conversation-item-conversation-2"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-test="conversation-page-error"]').text())
      .toContain('会话不存在。 [CONVERSATION_NOT_FOUND]')
    expect(conversations.messages).toEqual([])
  })

  it('会话加载失败后可以在专用页面强制重试', async () => {
    const { pinia, router, activate } = await setup()
    activate
      .mockRejectedValueOnce(new ApiError(503, 'SERVICE_UNAVAILABLE', '会话暂不可用。'))
      .mockResolvedValueOnce()
    const wrapper = mount(ConversationsView, {
      global: { plugins: [pinia, router, ElementPlus] },
    })
    await flushPromises()

    expect(wrapper.get('[data-test="conversation-page-error"]').text())
      .toContain('会话暂不可用。 [SERVICE_UNAVAILABLE]')
    await wrapper.get('[data-test="conversation-page-error"] button').trigger('click')
    await flushPromises()

    expect(activate).toHaveBeenLastCalledWith('u-1', 'kb-1', true)
    expect(wrapper.find('[data-test="conversation-page-error"]').exists()).toBe(false)
  })

  it('旧知识库迟到的加载失败不会覆盖新知识库状态', async () => {
    const { pinia, router, workspace, activate } = await setup()
    workspace.knowledgeBases.push({
      id: 'kb-2', name: '产品规范', description: null,
      owner_id: 'u-1', owner_username: 'alice',
    })
    let rejectOld!: (reason: unknown) => void
    activate
      .mockReturnValueOnce(new Promise((_resolve, reject) => { rejectOld = reject }))
      .mockResolvedValueOnce()
    const wrapper = mount(ConversationsView, {
      global: { plugins: [pinia, router, ElementPlus] },
    })
    await flushPromises()

    await router.push('/knowledge-bases/kb-2/conversations')
    await flushPromises()
    rejectOld(new ApiError(503, 'OLD_FAILURE', '旧请求失败'))
    await flushPromises()

    expect(activate).toHaveBeenLastCalledWith('u-1', 'kb-2', false)
    expect(wrapper.find('[data-test="conversation-page-error"]').exists()).toBe(false)
  })

  it('不渲染不属于当前用户的迟到知识库和会话状态', async () => {
    const { pinia, router, workspace, conversations } = await setup()
    workspace.knowledgeBases = [{
      id: 'kb-1', name: '其他用户知识库', description: null,
      owner_id: 'u-2', owner_username: 'bob',
    }]
    conversations.activeUserId = 'u-2'
    const wrapper = mount(ConversationsView, {
      global: { plugins: [pinia, router, ElementPlus] },
    })
    await flushPromises()

    expect(router.currentRoute.value.fullPath).toBe('/')
    expect(wrapper.find('[data-test="conversation-history"]').exists()).toBe(false)
    expect(wrapper.findComponent(QuestionPanel).exists()).toBe(false)
  })

  it('历史超过首批数量时可加载下一页', async () => {
    const { pinia, router, conversations } = await setup()
    conversations.total = 21
    conversations.currentPage = 1
    const loadConversations = vi.spyOn(conversations, 'loadConversations').mockResolvedValue()
    const wrapper = mount(ConversationsView, {
      global: { plugins: [pinia, router, ElementPlus] },
    })
    await flushPromises()

    await wrapper.get('[data-test="load-more-conversations"]').trigger('click')
    await flushPromises()

    expect(loadConversations).toHaveBeenCalledWith(2)
  })

  it('新建等结构性写操作期间禁止切换历史会话', async () => {
    const { pinia, router, conversations } = await setup()
    conversations.creating = true
    const openConversation = vi.spyOn(conversations, 'openConversation').mockResolvedValue()
    const wrapper = mount(ConversationsView, {
      global: { plugins: [pinia, router, ElementPlus] },
    })
    await flushPromises()

    expect(wrapper.get('[data-test="conversation-item-conversation-2"]').attributes())
      .toHaveProperty('disabled')
    await wrapper.get('[data-test="conversation-item-conversation-2"]').trigger('click')
    expect(openConversation).not.toHaveBeenCalled()
  })

  it('删除进行中禁止新建和清空历史', async () => {
    const { pinia, router, conversations } = await setup()
    let finishDelete!: () => void
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue({ action: 'confirm' } as never)
    vi.spyOn(conversations, 'deleteConversation').mockReturnValue(new Promise((resolve) => {
      finishDelete = resolve
    }))
    const wrapper = mount(ConversationsView, {
      global: { plugins: [pinia, router, ElementPlus] },
    })
    await flushPromises()

    await wrapper.get('[aria-label="删除会话 报销规则"]').trigger('click')
    await wrapper.vm.$nextTick()

    expect(wrapper.get('[data-test="new-conversation"]').attributes()).toHaveProperty('disabled')
    expect(wrapper.get('[data-test="clear-conversation"]').attributes()).toHaveProperty('disabled')

    finishDelete()
    await flushPromises()
  })
})

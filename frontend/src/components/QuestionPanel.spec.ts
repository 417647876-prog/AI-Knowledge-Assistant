import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus, { ElMessage, ElMessageBox } from 'element-plus'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/client'
import { useAuthStore } from '../stores/auth'
import { useConversationsStore } from '../stores/conversations'
import { useWorkspaceStore } from '../stores/workspace'
import type { AssistantMessage } from '../types/conversation'
import QuestionPanel from './QuestionPanel.vue'

describe('QuestionPanel', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    const auth = useAuthStore()
    const workspace = useWorkspaceStore()
    auth.user = { id: 'u-1', username: 'alice', role: 'user', is_active: true }
    workspace.activeKnowledgeBaseId = 'kb-1'
  })

  afterEach(() => {
    document.body.replaceChildren()
    vi.restoreAllMocks()
  })

  function mountPanel() {
    return mount(QuestionPanel, {
      attachTo: document.body,
      global: { plugins: [ElementPlus] },
    })
  }

  it('通过会话 Store 提交去空白后的问题并清空输入框', async () => {
    const conversations = useConversationsStore()
    const submit = vi.spyOn(conversations, 'submit').mockResolvedValue()
    const wrapper = mountPanel()

    await wrapper.get('textarea').setValue('  有多少天年假？  ')
    await wrapper.get('[data-test="submit-question"]').trigger('click')

    expect(submit).toHaveBeenCalledWith('有多少天年假？')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('')
  })

  it('流式回答期间将提问按钮切换为停止', async () => {
    const conversations = useConversationsStore()
    conversations.messages = [{
      id: 'answer-1', kind: 'assistant', questionId: 'question-1', content: '半段回答',
      createdAt: '2026-07-14T00:00:00Z', status: 'streaming', phase: 'generating',
      citations: [], standaloneQuestion: '问题', retrievedChunkCount: 1, timings: null,
      errorCode: null, requestId: null,
    } satisfies AssistantMessage]
    const stop = vi.spyOn(conversations, 'stop')
    const wrapper = mountPanel()

    expect(wrapper.get('[data-test="submit-question"]').text()).toContain('停止')
    await wrapper.get('[data-test="submit-question"]').trigger('click')

    expect(stop).toHaveBeenCalledOnce()
  })

  it('可新建分隔会话，并在确认后清空全部历史', async () => {
    const conversations = useConversationsStore()
    const newConversation = vi.spyOn(conversations, 'newConversation')
    const clear = vi.spyOn(conversations, 'clear')
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue({ action: 'confirm' } as never)
    const wrapper = mountPanel()

    await wrapper.get('[data-test="new-conversation"]').trigger('click')
    await wrapper.get('[data-test="clear-conversation"]').trigger('click')
    await flushPromises()

    expect(newConversation).toHaveBeenCalledOnce()
    expect(clear).toHaveBeenCalledOnce()
  })

  it('空白问题只提示而不提交', async () => {
    const conversations = useConversationsStore()
    const submit = vi.spyOn(conversations, 'submit')
    const warning = vi.spyOn(ElMessage, 'warning').mockImplementation(() => undefined as never)
    const wrapper = mountPanel()

    await wrapper.get('textarea').setValue('   ')
    await wrapper.get('[data-test="submit-question"]').trigger('click')

    expect(submit).not.toHaveBeenCalled()
    expect(warning).toHaveBeenCalledWith('请输入问题。')
  })

  it('提交异常时展示格式化后的错误信息', async () => {
    const conversations = useConversationsStore()
    vi.spyOn(conversations, 'submit').mockRejectedValue(
      new ApiError(503, 'MODEL_UNAVAILABLE', '模型服务暂不可用。', 'req-question-1'),
    )
    const error = vi.spyOn(ElMessage, 'error').mockImplementation(() => undefined as never)
    const wrapper = mountPanel()

    await wrapper.get('textarea').setValue('有多少天年假？')
    await wrapper.get('[data-test="submit-question"]').trigger('click')
    await flushPromises()

    expect(error).toHaveBeenCalledWith('模型服务暂不可用。 [MODEL_UNAVAILABLE] 请求标识：req-question-1')
  })

  it('未登录或未选择知识库时禁用提交按钮', () => {
    const auth = useAuthStore()
    auth.user = null
    const wrapper = mountPanel()

    expect(wrapper.get('textarea').attributes('maxlength')).toBe('2000')
    expect(wrapper.get('[data-test="submit-question"]').attributes()).toHaveProperty('disabled')
  })
})

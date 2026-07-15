import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ConversationMessage from './ConversationMessage.vue'
import type { AssistantMessage } from '../types/conversation'

function assistant(overrides: Partial<AssistantMessage> = {}): AssistantMessage {
  return {
    id: 'answer-1', kind: 'assistant', questionId: 'question-1', content: '初始回答',
    createdAt: '2026-07-14T00:00:00Z', status: 'streaming', phase: 'generating',
    citations: [], standaloneQuestion: null, retrievedChunkCount: null, timings: null,
    errorCode: null, requestId: null, ...overrides,
  }
}

describe('ConversationMessage', () => {
  afterEach(() => vi.useRealTimers())

  function mountMessage(message = assistant()) {
    return mount(ConversationMessage, { props: { message }, global: { plugins: [ElementPlus] } })
  }

  it('流式生成时显示阶段状态，并在五十毫秒后渲染 Markdown', async () => {
    vi.useFakeTimers()
    const wrapper = mountMessage(assistant({ content: '# 流式答案' }))

    expect(wrapper.text()).toContain('正在生成回答')
    expect(wrapper.find('.markdown-body').exists()).toBe(false)
    await vi.advanceTimersByTimeAsync(49)
    expect(wrapper.find('.markdown-body').exists()).toBe(false)
    await vi.advanceTimersByTimeAsync(1)
    expect(wrapper.get('.markdown-body').html()).toContain('<h1>流式答案</h1>')
  })

  it('节流期间始终渲染最新的流式增量', async () => {
    vi.useFakeTimers()
    const wrapper = mountMessage(assistant({ content: '第一段' }))

    await vi.advanceTimersByTimeAsync(49)
    await wrapper.setProps({ message: assistant({ content: '第一段第二段' }) })
    await vi.advanceTimersByTimeAsync(1)

    expect(wrapper.get('.markdown-body').text()).toBe('第一段第二段')
  })

  it('终态回答立即刷新安全 Markdown 并展示引用', async () => {
    vi.useFakeTimers()
    const wrapper = mountMessage(assistant({
      content: '<img src=x onerror=alert(1)> 已完成', status: 'streaming',
      citations: [{
        citation_id: 1, document_id: 'doc-1', file_name: '员工手册.txt', content: '年假规定',
        relevance_score: 0.91, page_number: 1, sheet_name: null, row_start: null, section_title: '年假',
      }],
    }))

    await wrapper.setProps({ message: assistant({
      content: '<img src=x onerror=alert(1)> 已完成', status: 'completed',
      citations: [{
        citation_id: 1, document_id: 'doc-1', file_name: '员工手册.txt', content: '年假规定',
        relevance_score: 0.91, page_number: 1, sheet_name: null, row_start: null, section_title: '年假',
      }],
    }) })
    await flushPromises()

    expect(wrapper.find('.markdown-body img').exists()).toBe(false)
    expect(wrapper.find('.markdown-body [onerror]').exists()).toBe(false)
    expect(wrapper.find('[data-test="citations"]').text()).toContain('员工手册.txt')
    expect(wrapper.find('[data-test="citations"]').text()).toContain('相关度 0.91')
  })

  it('失败时保留错误信息并透传重试事件', async () => {
    const wrapper = mountMessage(assistant({
      content: '半段回答', status: 'failed', phase: null,
      errorCode: 'CHAT_PROVIDER_ERROR', requestId: 'req-fail',
    }))

    expect(wrapper.text()).toContain('CHAT_PROVIDER_ERROR')
    expect(wrapper.text()).toContain('req-fail')
    await wrapper.get('[data-test="retry-answer"]').trigger('click')
    expect(wrapper.emitted('retry')).toEqual([['answer-1']])
  })
})

import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { afterEach, describe, expect, it } from 'vitest'
import ConversationTimeline from './ConversationTimeline.vue'
import type { ConversationMessage } from '../types/conversation'

const messages: ConversationMessage[] = [
  { id: 'question-1', kind: 'user', content: '它有什么缺点？', createdAt: '2026-07-14T00:00:00Z' },
  {
    id: 'answer-1', kind: 'assistant', questionId: 'question-1', content: '有两个缺点。',
    createdAt: '2026-07-14T00:00:01Z', status: 'completed', phase: null, citations: [],
    standaloneQuestion: '产品有什么缺点？', retrievedChunkCount: 1,
    timings: { rewrite_ms: 3, retrieval_ms: 5, generation_ms: 10, total_ms: 18 },
    errorCode: null, requestId: 'req-1',
  },
  { id: 'divider-1', kind: 'divider', createdAt: '2026-07-14T00:01:00Z' },
]

describe('ConversationTimeline', () => {
  afterEach(() => document.body.replaceChildren())

  it('按消息顺序展示问答、检索详情和会话分隔线', async () => {
    const wrapper = mount(ConversationTimeline, {
      props: { messages }, global: { plugins: [ElementPlus] },
    })

    expect(wrapper.text()).toContain('它有什么缺点？')
    expect(wrapper.text()).toContain('新会话')
    expect(wrapper.get('[data-test="retrieval-details"]').text()).toContain('检索详情')
    expect(wrapper.get('.el-collapse-item__header').attributes('aria-expanded')).toBe('false')

    await wrapper.get('.el-collapse-item__header').trigger('click')
    expect(wrapper.get('.el-collapse-item__header').attributes('aria-expanded')).toBe('true')
    expect(wrapper.text()).toContain('产品有什么缺点？')
    expect(wrapper.text()).toContain('检索片段数')
    expect(wrapper.text()).toContain('1')
  })

  it('透传回答的重试事件', async () => {
    const failedMessages = messages.map((message) => message.kind === 'assistant'
      ? { ...message, status: 'failed' as const, errorCode: 'CHAT_PROVIDER_ERROR', requestId: 'req-fail' }
      : message)
    const wrapper = mount(ConversationTimeline, {
      props: { messages: failedMessages }, global: { plugins: [ElementPlus] },
    })

    await wrapper.get('[data-test="retry-answer"]').trigger('click')
    expect(wrapper.emitted('retry')).toEqual([['answer-1']])
  })

  it('通过底部抽屉查看当前回答绑定的引用快照', async () => {
    const citedMessages = messages.map((message) => message.kind === 'assistant'
      ? {
          ...message,
          citations: [{
            citation_id: 2, document_id: 'doc-2', file_name: '研发规范.md',
            content: '发布前必须完成代码审查。', relevance_score: 0.88,
            page_number: null, sheet_name: null, row_start: null, section_title: '发布流程',
          }],
        }
      : message)
    const wrapper = mount(ConversationTimeline, {
      attachTo: document.body,
      props: { messages: citedMessages }, global: { plugins: [ElementPlus] },
    })

    expect(wrapper.find('[data-test="citations"]').exists()).toBe(false)
    await wrapper.get('[data-test="open-citations-answer-1"]').trigger('click')
    await flushPromises()

    expect(document.body.textContent).toContain('引用来源')
    expect(document.body.textContent).toContain('研发规范.md')
    expect(document.body.textContent).toContain('发布前必须完成代码审查。')
  })
})

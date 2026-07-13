import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus, { ElMessage } from 'element-plus'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/knowledgeBases', () => ({
  listKnowledgeBases: vi.fn(), createKnowledgeBase: vi.fn(),
}))
vi.mock('../api/documents', () => ({
  uploadDocument: vi.fn(), pollDocumentStatus: vi.fn(),
}))
vi.mock('../api/questions', () => ({ askQuestion: vi.fn() }))

import { ApiError } from '../api/client'
import { useWorkspaceStore } from '../stores/workspace'
import QuestionPanel from './QuestionPanel.vue'

describe('QuestionPanel', () => {
  beforeEach(() => setActivePinia(createPinia()))
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

  it('展示答案、检索信息和完整引用元数据', () => {
    const store = useWorkspaceStore()
    store.answer = {
      answer: '员工有 5 天年假。[1]',
      retrieved_chunk_count: 1,
      request_id: 'req-1',
      citations: [{
        citation_id: 1,
        document_id: 'doc-1',
        file_name: '年假制度.txt',
        content: '员工每年享有 5 天年假。',
        relevance_score: 0.91,
        page_number: 1,
        sheet_name: '假期规则',
        row_start: 8,
        section_title: '年假',
      }],
    }

    const wrapper = mountPanel()

    expect(wrapper.text()).toContain('员工有 5 天年假')
    expect(wrapper.text()).toContain('年假制度.txt')
    expect(wrapper.text()).toContain('第 1 页')
    expect(wrapper.text()).toContain('工作表：假期规则')
    expect(wrapper.text()).toContain('行号：8')
    expect(wrapper.text()).toContain('相关度 0.91')
    expect(wrapper.text()).toContain('检索片段数：1')
    expect(wrapper.text()).toContain('请求标识：req-1')
  })

  it('引用元数据为空时不展示对应标签', () => {
    const store = useWorkspaceStore()
    store.answer = {
      answer: '制度中有相关说明。[1]',
      retrieved_chunk_count: 1,
      request_id: 'req-2',
      citations: [{
        citation_id: 1,
        document_id: 'doc-1',
        file_name: '制度.txt',
        content: '相关说明',
        relevance_score: 0.8,
        page_number: null,
        sheet_name: null,
        row_start: null,
        section_title: null,
      }],
    }

    const wrapper = mountPanel()

    expect(wrapper.text()).not.toContain('第 null 页')
    expect(wrapper.text()).not.toContain('工作表：')
    expect(wrapper.text()).not.toContain('行号：')
  })

  it('无引用的拒答不渲染引用容器', () => {
    const store = useWorkspaceStore()
    store.answer = {
      answer: '知识库中没有足够信息回答该问题。',
      retrieved_chunk_count: 0,
      request_id: 'req-empty',
      citations: [],
    }

    const wrapper = mountPanel()

    expect(wrapper.text()).toContain('知识库中没有足够信息回答该问题。')
    expect(wrapper.find('[data-test="citations"]').exists()).toBe(false)
  })

  it('问题为空白时提示且不提交', async () => {
    const warning = vi.spyOn(ElMessage, 'warning').mockImplementation(() => undefined as never)
    const store = useWorkspaceStore()
    store.activeKnowledgeBaseId = 'kb-1'
    const submitQuestion = vi.spyOn(store, 'submitQuestion')
    const wrapper = mountPanel()

    await wrapper.get('textarea').setValue('   ')
    await wrapper.get('[data-test="submit-question"]').trigger('click')

    expect(submitQuestion).not.toHaveBeenCalled()
    expect(warning).toHaveBeenCalledWith('请输入问题。')
  })

  it('提交失败时展示错误代码和请求标识', async () => {
    const store = useWorkspaceStore()
    store.activeKnowledgeBaseId = 'kb-1'
    vi.spyOn(store, 'submitQuestion').mockRejectedValue(
      new ApiError(503, 'MODEL_UNAVAILABLE', '模型服务暂不可用。', 'req-question-1'),
    )
    const wrapper = mountPanel()

    await wrapper.get('textarea').setValue('有多少天年假？')
    await wrapper.get('[data-test="submit-question"]').trigger('click')
    await flushPromises()

    expect(document.body.textContent).toContain('MODEL_UNAVAILABLE')
    expect(document.body.textContent).toContain('req-question-1')
  })

  it('未选择知识库时禁用提交按钮并限制问题长度', () => {
    const wrapper = mountPanel()

    expect(wrapper.get('textarea').attributes('maxlength')).toBe('2000')
    expect(wrapper.get('[data-test="submit-question"]').attributes()).toHaveProperty('disabled')
  })
})

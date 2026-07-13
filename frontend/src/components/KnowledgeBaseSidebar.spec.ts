import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/knowledgeBases', () => ({
  listKnowledgeBases: vi.fn(), createKnowledgeBase: vi.fn(),
}))
vi.mock('../api/documents', () => ({
  uploadDocument: vi.fn(), pollDocumentStatus: vi.fn(),
}))

import { ApiError } from '../api/client'
import { createKnowledgeBase } from '../api/knowledgeBases'
import { useWorkspaceStore } from '../stores/workspace'
import KnowledgeBaseSidebar from './KnowledgeBaseSidebar.vue'

describe('KnowledgeBaseSidebar', () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => document.body.replaceChildren())

  function mountSidebar() {
    return mount(KnowledgeBaseSidebar, {
      attachTo: document.body,
      global: { plugins: [ElementPlus], stubs: { teleport: true } },
    })
  }

  it('展示知识库并允许选择', async () => {
    const store = useWorkspaceStore()
    store.knowledgeBases = [{ id: 'kb-1', name: '人事制度', description: null }]
    const wrapper = mountSidebar()

    expect(wrapper.text()).toContain('人事制度')
    await wrapper.get('.el-menu-item').trigger('click')

    expect(store.activeKnowledgeBaseId).toBe('kb-1')
  })

  it('为长知识库名称提供可截断样式和完整标题', () => {
    const store = useWorkspaceStore()
    const longName = '这是一个非常非常长且不包含空格的知识库名称'
    store.knowledgeBases = [{ id: 'kb-long', name: longName, description: null }]

    const wrapper = mountSidebar()

    const name = wrapper.get('.knowledge-base-name')
    expect(name.text()).toBe(longName)
    expect(name.attributes('title')).toBe(longName)
  })

  it('点击新建按钮后显示对话框', async () => {
    const wrapper = mountSidebar()

    await wrapper.get('[data-test="create-knowledge-base"]').trigger('click')

    expect(wrapper.get('[data-test="knowledge-base-dialog"]').isVisible()).toBe(true)
  })

  it('创建知识库后关闭对话框并清空表单', async () => {
    vi.mocked(createKnowledgeBase).mockResolvedValue({
      id: 'kb-2', name: '研发规范', description: '研发资料',
    })
    const wrapper = mountSidebar()
    await wrapper.get('[data-test="create-knowledge-base"]').trigger('click')
    const inputs = wrapper.findAll('input')
    await inputs[0]!.setValue('  研发规范  ')
    await wrapper.get('textarea').setValue('  研发资料  ')

    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(createKnowledgeBase).toHaveBeenCalledWith({
      name: '研发规范', description: '研发资料',
    })
    expect(wrapper.get('[data-test="knowledge-base-dialog"]').isVisible()).toBe(false)
    await wrapper.get('[data-test="create-knowledge-base"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('input').element.value).toBe('')
    expect(wrapper.get('textarea').element.value).toBe('')
  })

  it('创建失败时显示错误代码和请求标识', async () => {
    vi.mocked(createKnowledgeBase).mockRejectedValue(
      new ApiError(409, 'KNOWLEDGE_BASE_EXISTS', '知识库已存在。', 'req-kb-1'),
    )
    const wrapper = mountSidebar()
    await wrapper.get('[data-test="create-knowledge-base"]').trigger('click')
    await wrapper.get('input').setValue('人事制度')

    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(document.body.textContent).toContain('KNOWLEDGE_BASE_EXISTS')
    expect(document.body.textContent).toContain('req-kb-1')
  })
})

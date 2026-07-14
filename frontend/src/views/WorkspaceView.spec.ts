import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import DocumentTable from '../components/DocumentTable.vue'
import DocumentUpload from '../components/DocumentUpload.vue'
import KnowledgeBaseSidebar from '../components/KnowledgeBaseSidebar.vue'
import QuestionPanel from '../components/QuestionPanel.vue'
import { useWorkspaceStore } from '../stores/workspace'
import WorkspaceView from './WorkspaceView.vue'

describe('WorkspaceView', () => {
  it('组装知识库工作台并在挂载时加载知识库与当前文档', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useWorkspaceStore()
    store.knowledgeBases = [{
      id: 'kb-1', name: '研发规范', description: null,
      owner_id: 'u-1', owner_username: 'alice',
    }]
    store.activeKnowledgeBaseId = 'kb-1'
    const loadKnowledgeBases = vi.spyOn(store, 'loadKnowledgeBases').mockResolvedValue()
    const loadDocuments = vi.spyOn(store, 'loadDocuments').mockResolvedValue()

    const wrapper = mount(WorkspaceView, { global: { plugins: [pinia, ElementPlus] } })
    await flushPromises()

    expect(wrapper.findComponent(KnowledgeBaseSidebar).exists()).toBe(true)
    expect(wrapper.findComponent(DocumentUpload).exists()).toBe(true)
    expect(wrapper.findComponent(DocumentTable).exists()).toBe(true)
    expect(wrapper.findComponent(QuestionPanel).exists()).toBe(true)
    expect(wrapper.text()).not.toContain('请选择或创建知识库')
    expect(loadKnowledgeBases).toHaveBeenCalledOnce()
    expect(loadDocuments).toHaveBeenCalledOnce()
  })

  it('未选择知识库时保留侧栏并显示选择提示', () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useWorkspaceStore()
    vi.spyOn(store, 'loadKnowledgeBases').mockResolvedValue()

    const wrapper = mount(WorkspaceView, { global: { plugins: [pinia, ElementPlus] } })

    expect(wrapper.findComponent(KnowledgeBaseSidebar).exists()).toBe(true)
    expect(wrapper.text()).toContain('请选择或创建知识库')
    expect(wrapper.findComponent(DocumentUpload).exists()).toBe(false)
    expect(wrapper.findComponent(DocumentTable).exists()).toBe(false)
    expect(wrapper.findComponent(QuestionPanel).exists()).toBe(false)
  })

  it('初始加载失败时展示格式化错误并允许重新加载', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useWorkspaceStore()
    const loadKnowledgeBases = vi.spyOn(store, 'loadKnowledgeBases')
      .mockRejectedValueOnce(new ApiError(
        500, 'HTTP_ERROR', '服务暂不可用，请稍后重试。', 'req-load-1',
      ))
      .mockResolvedValueOnce()

    const wrapper = mount(WorkspaceView, { global: { plugins: [pinia, ElementPlus] } })
    await flushPromises()

    expect(wrapper.get('[data-test="knowledge-base-load-error"]').text()).toContain(
      '服务暂不可用，请稍后重试。 [HTTP_ERROR] 请求标识：req-load-1',
    )

    await wrapper.get('[data-test="reload-knowledge-bases"]').trigger('click')
    await flushPromises()

    expect(loadKnowledgeBases).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[data-test="knowledge-base-load-error"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('请选择或创建知识库')
  })
})

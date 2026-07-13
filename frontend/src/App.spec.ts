import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { describe, expect, it, vi } from 'vitest'
import App from './App.vue'
import DocumentTable from './components/DocumentTable.vue'
import DocumentUpload from './components/DocumentUpload.vue'
import KnowledgeBaseSidebar from './components/KnowledgeBaseSidebar.vue'
import QuestionPanel from './components/QuestionPanel.vue'
import { useWorkspaceStore } from './stores/workspace'

describe('App', () => {
  it('组装知识库工作台并在挂载时加载知识库', () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useWorkspaceStore()
    store.knowledgeBases = [{ id: 'kb-1', name: '研发规范', description: null }]
    store.activeKnowledgeBaseId = 'kb-1'
    const loadKnowledgeBases = vi.spyOn(store, 'loadKnowledgeBases').mockResolvedValue()

    const wrapper = mount(App, { global: { plugins: [pinia, ElementPlus] } })

    expect(wrapper.get('h1').text()).toBe('AI 知识库助手')
    expect(wrapper.findComponent(KnowledgeBaseSidebar).exists()).toBe(true)
    expect(wrapper.findComponent(DocumentUpload).exists()).toBe(true)
    expect(wrapper.findComponent(DocumentTable).exists()).toBe(true)
    expect(wrapper.findComponent(QuestionPanel).exists()).toBe(true)
    expect(loadKnowledgeBases).toHaveBeenCalledOnce()
  })

  it('未选择知识库时保留侧栏并显示选择提示', () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useWorkspaceStore()
    vi.spyOn(store, 'loadKnowledgeBases').mockResolvedValue()

    const wrapper = mount(App, { global: { plugins: [pinia, ElementPlus] } })

    expect(wrapper.findComponent(KnowledgeBaseSidebar).exists()).toBe(true)
    expect(wrapper.text()).toContain('请选择或创建知识库')
    expect(wrapper.findComponent(DocumentUpload).exists()).toBe(false)
    expect(wrapper.findComponent(DocumentTable).exists()).toBe(false)
    expect(wrapper.findComponent(QuestionPanel).exists()).toBe(false)
  })
})

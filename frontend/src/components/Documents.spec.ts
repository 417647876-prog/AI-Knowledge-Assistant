import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus, { ElMessage, ElMessageBox } from 'element-plus'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'

vi.mock('../api/knowledgeBases', () => ({
  listKnowledgeBases: vi.fn(), createKnowledgeBase: vi.fn(),
}))
vi.mock('../api/documents', () => ({
  uploadDocument: vi.fn(), pollDocumentStatus: vi.fn(),
}))

import { ApiError } from '../api/client'
import { uploadDocument } from '../api/documents'
import { useWorkspaceStore } from '../stores/workspace'
import DocumentTable from './DocumentTable.vue'
import DocumentUpload from './DocumentUpload.vue'

const Documents = defineComponent({
  components: { DocumentUpload, DocumentTable },
  template: '<DocumentUpload /><DocumentTable />',
})

describe('文档区域', () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => document.body.replaceChildren())

  function mountDocuments() {
    return mount(Documents, {
      attachTo: document.body,
      global: { plugins: [ElementPlus] },
    })
  }

  it('限制上传格式并展示文档状态', async () => {
    const store = useWorkspaceStore()
    store.activeKnowledgeBaseId = 'kb-1'
    store.documents['kb-1'] = [
      { document_id: 'doc-1', job_id: 'job-1', status: 'pending', error_code: null,
        error_message: null, file_name: '待处理.txt' },
      { document_id: 'doc-2', job_id: 'job-2', status: 'parsing', error_code: null,
        error_message: null, file_name: '制度.txt' },
      { document_id: 'doc-3', job_id: 'job-3', status: 'embedding', error_code: null,
        error_message: null, file_name: '向量化中.txt' },
      { document_id: 'doc-4', job_id: 'job-4', status: 'ready', error_code: null,
        error_message: null, file_name: '可用.txt' },
      { document_id: 'doc-5', job_id: 'job-5', status: 'failed', error_code: 'PARSE_FAILED',
        error_message: '无法解析文档。', file_name: '失败.txt' },
    ]

    const wrapper = mountDocuments()
    await flushPromises()

    expect(wrapper.get('input[type="file"]').attributes('accept')).toBe('.txt,.md,.pdf,.docx,.xlsx')
    expect(wrapper.text()).toContain('解析中')
    expect(wrapper.text()).toContain('向量化中')
    expect(wrapper.text()).toContain('制度.txt')
    expect(wrapper.text()).toContain('等待处理')
    expect(wrapper.text()).toContain('可用')
    expect(wrapper.text()).toContain('处理失败')
    expect(wrapper.text()).toContain('PARSE_FAILED')
    expect(wrapper.text()).toContain('无法解析文档。')
  })

  it('没有文档时显示空状态', () => {
    const store = useWorkspaceStore()
    store.activeKnowledgeBaseId = 'kb-1'

    expect(mountDocuments().text()).toContain('当前知识库暂无文档')
  })

  it('上传失败时保留错误代码和请求标识', async () => {
    vi.mocked(uploadDocument).mockRejectedValue(
      new ApiError(413, 'FILE_TOO_LARGE', '文件过大。', 'req-upload-1'),
    )
    const store = useWorkspaceStore()
    store.activeKnowledgeBaseId = 'kb-1'
    const wrapper = mountDocuments()
    const input = wrapper.get('input[type="file"]')
    const file = new File(['制度'], '制度.txt', { type: 'text/plain' })
    Object.defineProperty(input.element, 'files', { value: [file] })

    await input.trigger('change')
    await flushPromises()

    expect(document.body.textContent).toContain('FILE_TOO_LARGE')
    expect(document.body.textContent).toContain('req-upload-1')
  })

  it('上传进行中不会重复提交文件', async () => {
    vi.mocked(uploadDocument).mockReturnValue(new Promise(() => {}))
    const store = useWorkspaceStore()
    store.activeKnowledgeBaseId = 'kb-1'
    const wrapper = mountDocuments()
    const input = wrapper.get('input[type="file"]')
    const file = new File(['制度'], '制度.txt', { type: 'text/plain' })
    Object.defineProperty(input.element, 'files', { value: [file] })

    await input.trigger('change')
    await input.trigger('change')

    expect(uploadDocument).toHaveBeenCalledTimes(1)
    expect(input.attributes()).toHaveProperty('disabled')
  })

  it('可用和失败文档允许重处理，处理中文档禁止重处理和删除', async () => {
    const store = useWorkspaceStore()
    store.activeKnowledgeBaseId = 'kb-1'
    store.documents['kb-1'] = [
      { document_id: 'doc-processing', job_id: 'job-1', file_name: '处理中.txt', status: 'parsing',
        error_code: null, error_message: null },
      { document_id: 'doc-ready', job_id: 'job-ready', file_name: '可用.txt', status: 'ready',
        error_code: null, error_message: null },
      { document_id: 'doc-failed', job_id: 'job-2', file_name: '失败.txt', status: 'failed',
        error_code: 'PARSE_FAILED', error_message: '无法解析文档。' },
    ]
    const reprocess = vi.spyOn(store, 'reprocessDocument').mockResolvedValue({
      ...store.documents['kb-1']![2]!, status: 'ready',
    })
    const wrapper = mountDocuments()
    await flushPromises()

    await wrapper.get('[data-test="reprocess-doc-failed"]').trigger('click')

    expect(reprocess).toHaveBeenCalledWith('doc-failed')
    expect(wrapper.get('[data-test="reprocess-doc-ready"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('[data-test="reprocess-doc-processing"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-test="delete-doc-processing"]').attributes('disabled')).toBeDefined()
  })

  it('重处理进行中禁用当前行，避免重复提交', async () => {
    const store = useWorkspaceStore()
    store.activeKnowledgeBaseId = 'kb-1'
    store.documents['kb-1'] = [{
      document_id: 'doc-ready', job_id: 'job-1', file_name: '可用.txt', status: 'ready',
      error_code: null, error_message: null,
    }]
    vi.spyOn(store, 'reprocessDocument').mockReturnValue(new Promise(() => {}))
    const wrapper = mountDocuments()
    await flushPromises()
    const button = wrapper.get('[data-test="reprocess-doc-ready"]')

    await button.trigger('click')
    await button.trigger('click')

    expect(store.reprocessDocument).toHaveBeenCalledTimes(1)
    expect(button.attributes('disabled')).toBeDefined()
  })

  it('确认删除后调用 Store，取消时不调用', async () => {
    const store = useWorkspaceStore()
    store.activeKnowledgeBaseId = 'kb-1'
    store.documents['kb-1'] = [{
      document_id: 'doc-ready', job_id: 'job-1', file_name: '可用.txt', status: 'ready',
      error_code: null, error_message: null,
    }]
    const removeDocument = vi.spyOn(store, 'deleteDocument').mockResolvedValue()
    const confirm = vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue(undefined as never)
    const wrapper = mountDocuments()
    await flushPromises()

    await wrapper.get('[data-test="delete-doc-ready"]').trigger('click')
    await flushPromises()
    expect(confirm).toHaveBeenCalled()
    expect(removeDocument).toHaveBeenCalledWith('doc-ready')

    removeDocument.mockClear()
    confirm.mockRejectedValueOnce('cancel')
    await wrapper.get('[data-test="delete-doc-ready"]').trigger('click')
    await flushPromises()
    expect(removeDocument).not.toHaveBeenCalled()
  })

  it('操作失败时显示错误代码和请求标识', async () => {
    const store = useWorkspaceStore()
    store.activeKnowledgeBaseId = 'kb-1'
    store.documents['kb-1'] = [{
      document_id: 'doc-ready', job_id: 'job-1', file_name: '可用.txt', status: 'ready',
      error_code: null, error_message: null,
    }]
    vi.spyOn(store, 'reprocessDocument').mockRejectedValue(
      new ApiError(409, 'DOCUMENT_PROCESSING', '文档正在处理中。', 'req-action-1'),
    )
    const showError = vi.spyOn(ElMessage, 'error').mockImplementation(() => undefined as never)
    const wrapper = mountDocuments()
    await flushPromises()

    await wrapper.get('[data-test="reprocess-doc-ready"]').trigger('click')
    await flushPromises()

    expect(showError).toHaveBeenCalledWith(
      '文档正在处理中。 [DOCUMENT_PROCESSING] 请求标识：req-action-1',
    )
  })
})

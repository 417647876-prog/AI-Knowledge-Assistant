import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useAdminOperationsStore } from '../stores/adminOperations'
import AdminOperationsView from './AdminOperationsView.vue'

describe('AdminOperationsView', () => {
  afterEach(() => vi.restoreAllMocks())

  it('只渲染脱敏运营字段和稳定错误码', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useAdminOperationsStore()
    store.overview = {
      account_total: 2, active_account_total: 2, knowledge_base_total: 3,
      document_total: 5, effective_document_bytes: 1024, job_status_counts: { failed: 1 },
      token_total: 90, cost_total: '0.02', feedback: { helpful: 2, unhelpful: 1 },
      risk_event_total: 0, system_health: { worker_status_counts: { ready: 1 } },
    }
    store.users = [{
      user_id: 'u-1', username: 'alice', role: 'user', is_active: true,
      knowledge_base_total: 2, document_total: 5, effective_document_bytes: 1024,
      job_total: 6, token_total: 90, cost_total: '0.02',
    }]
    store.jobs = {
      items: [{
        id: 'job-1', resource_type: 'document', status: 'failed', stage: 'embedding',
        attempt_count: 3, error_code: 'EMBEDDING_FAILED', created_at: '2026-07-22T00:00:00Z',
      }],
      next_cursor: null,
    }
    store.quality = {
      latest_offline_evaluation: null,
      online_agent_metrics: { observation_total: 3, valid_citation_total: 2 },
      feedback_distribution: { helpful: 2, unhelpful: 1 },
    }
    vi.spyOn(store, 'load').mockResolvedValue()
    const wrapper = mount(AdminOperationsView, { global: { plugins: [pinia, ElementPlus] } })
    await flushPromises()

    expect(wrapper.text()).toContain('alice')
    expect(wrapper.text()).toContain('EMBEDDING_FAILED')
    expect(wrapper.text()).toContain('质量聚合')
    expect(wrapper.text()).not.toMatch(/知识库名称|文件名称|问题正文|回答正文|Prompt|下载地址/)
  })

  it('挂载时加载默认时间范围并支持重新查询', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useAdminOperationsStore()
    const load = vi.spyOn(store, 'load').mockResolvedValue()
    const wrapper = mount(AdminOperationsView, { global: { plugins: [pinia, ElementPlus] } })
    await flushPromises()

    expect(load).toHaveBeenCalledOnce()
    await wrapper.get('[data-test="refresh-operations"]').trigger('click')
    await flushPromises()
    expect(load).toHaveBeenCalledTimes(2)
  })
})

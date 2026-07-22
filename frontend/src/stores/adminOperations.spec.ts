import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/adminOperations', () => ({
  getOperationsOverview: vi.fn(),
  listUserOperations: vi.fn(),
  getOperationsJobs: vi.fn(),
  getOperationsQuality: vi.fn(),
}))

import {
  getOperationsJobs,
  getOperationsOverview,
  getOperationsQuality,
  listUserOperations,
} from '../api/adminOperations'
import { useAdminOperationsStore } from './adminOperations'

const range = { startAt: '2026-07-01T00:00:00Z', endAt: '2026-07-22T00:00:00Z' }

describe('admin operations store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('按同一时间范围加载脱敏总览、用户、任务和质量聚合', async () => {
    vi.mocked(getOperationsOverview).mockResolvedValue({
      account_total: 2, active_account_total: 2, knowledge_base_total: 3,
      document_total: 5, effective_document_bytes: 1024, job_status_counts: { succeeded: 4 },
      token_total: 90, cost_total: '0.02', feedback: { helpful: 2 }, risk_event_total: 0,
      system_health: { worker_status_counts: { ready: 1 } },
    })
    vi.mocked(listUserOperations).mockResolvedValue([])
    vi.mocked(getOperationsJobs).mockResolvedValue({ items: [], next_cursor: null })
    vi.mocked(getOperationsQuality).mockResolvedValue({
      latest_offline_evaluation: null,
      online_agent_metrics: { observation_total: 0 }, feedback_distribution: {},
    })
    const store = useAdminOperationsStore()

    await store.load(range)

    expect(getOperationsOverview).toHaveBeenCalledWith(range)
    expect(listUserOperations).toHaveBeenCalledWith(range)
    expect(getOperationsJobs).toHaveBeenCalledWith({ ...range, limit: 20 })
    expect(getOperationsQuality).toHaveBeenCalledWith(range)
    expect(store.overview?.account_total).toBe(2)
  })

  it('任务下一页使用服务端复合游标并追加结果', async () => {
    vi.mocked(getOperationsJobs).mockResolvedValue({
      items: [{
        id: 'job-2', resource_type: 'document', status: 'failed', stage: 'embedding',
        attempt_count: 3, error_code: 'EMBEDDING_FAILED', created_at: '2026-07-21T00:00:00Z',
      }],
      next_cursor: null,
    })
    const store = useAdminOperationsStore()
    store.range = range
    store.jobs = {
      items: [{
        id: 'job-1', resource_type: 'document', status: 'succeeded', stage: 'completed',
        attempt_count: 1, error_code: null, created_at: '2026-07-22T00:00:00Z',
      }],
      next_cursor: { created_at: '2026-07-22T00:00:00Z', id: 'job-1' },
    }

    await store.loadMoreJobs()

    expect(getOperationsJobs).toHaveBeenCalledWith({
      ...range, limit: 20, cursorCreatedAt: '2026-07-22T00:00:00Z', cursorId: 'job-1',
    })
    expect(store.jobs.items.map((item) => item.id)).toEqual(['job-1', 'job-2'])
  })
})

import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { FeedbackPage, QuotaResponse, UsageSummary } from '../types/api'

vi.mock('../api/me', () => ({
  getMyQuota: vi.fn(),
  getMyUsage: vi.fn(),
  listMyFeedback: vi.fn(),
  removeMessageFeedback: vi.fn(),
}))

import { getMyQuota, getMyUsage, listMyFeedback, removeMessageFeedback } from '../api/me'
import { useProfileStore } from './profile'

const quota: QuotaResponse = {
  defaults: { daily_question_limit: 20, daily_upload_limit: 5, storage_bytes_limit: 1024 },
  overrides: { daily_question_limit: null, daily_upload_limit: 3, storage_bytes_limit: null },
  used: { question_count: 4, upload_count: 1, storage_bytes_used: 256 },
  remaining: { question_count: 16, upload_count: 2, storage_bytes: 768 },
}

const usage: UsageSummary = {
  from: '2026-07-21T16:00:00Z', to: '2026-07-22T16:00:00Z',
  tokens: {
    cache_hit_input_tokens: 10, cache_miss_input_tokens: 20,
    output_tokens: 30, reasoning_tokens: 5, total_tokens: 60,
  },
  estimated_cost: '0.001200', usage_unknown_count: 1,
  purposes: {
    answer: { event_count: 2, total_tokens: 60, estimated_cost: '0.001200', usage_unknown_count: 1 },
  },
}

const feedback: FeedbackPage = {
  items: [{
    id: 'feedback-1', message_id: 'message-1', helpful: false,
    reason: 'unhelpful_missing', created_at: '2026-07-22T08:00:00Z',
    updated_at: '2026-07-22T08:00:00Z',
  }],
  page: 1, page_size: 10, total: 1,
}

describe('profile store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('按上海自然日加载额度、用量和当前用户反馈', async () => {
    vi.mocked(getMyQuota).mockResolvedValue(quota)
    vi.mocked(getMyUsage).mockResolvedValue(usage)
    vi.mocked(listMyFeedback).mockResolvedValue(feedback)
    const store = useProfileStore()

    await store.load('u-1', new Date('2026-07-22T12:30:00Z'))

    expect(getMyUsage).toHaveBeenCalledWith(
      '2026-07-21T16:00:00.000Z',
      '2026-07-22T16:00:00.000Z',
    )
    expect(listMyFeedback).toHaveBeenCalledWith({ page: 1, pageSize: 10 })
    expect(store.activeUserId).toBe('u-1')
    expect(store.quota).toEqual(quota)
    expect(store.usage).toEqual(usage)
    expect(store.feedback.items).toEqual(feedback.items)
    expect(store.resetAt).toBe('2026-07-22T16:00:00.000Z')
  })

  it('迟到的旧用户响应不能覆盖新用户状态', async () => {
    let resolveOld!: (value: QuotaResponse) => void
    vi.mocked(getMyQuota)
      .mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve }))
      .mockResolvedValueOnce(quota)
    vi.mocked(getMyUsage).mockResolvedValue(usage)
    vi.mocked(listMyFeedback).mockResolvedValue(feedback)
    const store = useProfileStore()

    const oldLoad = store.load('u-1')
    await vi.waitFor(() => expect(getMyQuota).toHaveBeenCalledOnce())
    await store.load('u-2')
    resolveOld(quota)
    await oldLoad

    expect(store.activeUserId).toBe('u-2')
  })

  it('删除反馈后只移除后端已确认的当前用户记录', async () => {
    vi.mocked(removeMessageFeedback).mockResolvedValue()
    const store = useProfileStore()
    store.activeUserId = 'u-1'
    store.feedback = feedback

    await store.deleteFeedback('message-1')

    expect(removeMessageFeedback).toHaveBeenCalledWith('message-1')
    expect(store.feedback.items).toEqual([])
    expect(store.feedback.total).toBe(0)
  })

  it('分页只请求指定页并保留服务端 owner 过滤结果', async () => {
    vi.mocked(listMyFeedback).mockResolvedValue({ ...feedback, page: 2 })
    const store = useProfileStore()
    store.activeUserId = 'u-1'

    await store.loadFeedback(2)

    expect(listMyFeedback).toHaveBeenCalledWith({ page: 2, pageSize: 10 })
    expect(store.feedback.page).toBe(2)
  })
})

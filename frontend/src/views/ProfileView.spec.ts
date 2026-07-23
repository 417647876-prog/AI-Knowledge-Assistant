import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus, { ElMessageBox } from 'element-plus'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '../stores/auth'
import { useProfileStore } from '../stores/profile'
import ProfileView from './ProfileView.vue'

function setup(unknownCount = 0) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.user = { id: 'u-1', username: 'alice', role: 'user', is_active: true }
  const profile = useProfileStore()
  profile.quota = {
    defaults: { daily_question_limit: 20, daily_upload_limit: 5, storage_bytes_limit: 1073741824 },
    overrides: { daily_question_limit: null, daily_upload_limit: 3, storage_bytes_limit: null },
    used: { question_count: 4, upload_count: 1, storage_bytes_used: 268435456 },
    remaining: { question_count: 16, upload_count: 2, storage_bytes: 805306368 },
  }
  profile.usage = {
    from: '2026-07-21T16:00:00Z', to: '2026-07-22T16:00:00Z',
    tokens: {
      cache_hit_input_tokens: 0, cache_miss_input_tokens: 0,
      output_tokens: 0, reasoning_tokens: 0, total_tokens: 0,
    },
    estimated_cost: '0.000000', usage_unknown_count: unknownCount,
    purposes: {},
  }
  profile.feedback = {
    items: [{
      id: 'feedback-1', message_id: 'message-1', helpful: false,
      reason: 'unhelpful_missing', created_at: '2026-07-22T08:00:00Z',
      updated_at: '2026-07-22T08:00:00Z',
    }],
    page: 1, page_size: 10, total: 1,
  }
  profile.resetAt = '2026-07-22T16:00:00Z'
  vi.spyOn(profile, 'load').mockResolvedValue()
  vi.spyOn(profile, 'loadFeedback').mockResolvedValue()
  vi.spyOn(profile, 'deleteFeedback').mockResolvedValue()
  return { pinia, profile }
}

describe('ProfileView', () => {
  afterEach(() => vi.restoreAllMocks())

  it('展示问答、上传、存储额度和上海时区重置边界', async () => {
    const { pinia, profile } = setup()
    const wrapper = mount(ProfileView, { global: { plugins: [pinia, ElementPlus] } })
    await flushPromises()

    expect(profile.load).toHaveBeenCalledWith('u-1')
    expect(wrapper.get('[data-test="quota-summary"]').text()).toContain('4 / 20')
    expect(wrapper.get('[data-test="quota-summary"]').text()).toContain('1 / 3')
    expect(wrapper.get('[data-test="quota-summary"]').text()).toContain('256 MB / 1 GB')
    expect(wrapper.get('[data-test="quota-reset"]').text()).toContain('上海时间')
  })

  it('存在未知 usage 时 Token 和费用明确显示未知而不是 0', async () => {
    const { pinia } = setup(1)
    const wrapper = mount(ProfileView, { global: { plugins: [pinia, ElementPlus] } })
    await flushPromises()

    expect(wrapper.get('[data-test="usage-tokens"]').text()).toBe('未知')
    expect(wrapper.get('[data-test="usage-cost"]').text()).toBe('未知')
    expect(wrapper.text()).not.toContain('¥0.000000')
  })

  it('删除反馈经过确认并只把 message id 交给 Store', async () => {
    const { pinia, profile } = setup()
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue({ action: 'confirm' } as never)
    const wrapper = mount(ProfileView, { global: { plugins: [pinia, ElementPlus] } })
    await flushPromises()

    await wrapper.get('[data-test="delete-feedback-message-1"]').trigger('click')
    await flushPromises()

    expect(profile.deleteFeedback).toHaveBeenCalledWith('message-1')
  })
})

import { ref } from 'vue'
import { defineStore } from 'pinia'
import {
  getMyQuota,
  getMyUsage,
  listMyFeedback,
  removeMessageFeedback,
} from '../api/me'
import type { FeedbackPage, QuotaResponse, UsageSummary } from '../types/api'

const feedbackPageSize = 10

const emptyFeedback = (): FeedbackPage => ({
  items: [], page: 1, page_size: feedbackPageSize, total: 0,
})

function shanghaiDayRange(now: Date): { from: string; to: string } {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(now)
  const value = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? ''
  const start = new Date(`${value('year')}-${value('month')}-${value('day')}T00:00:00+08:00`)
  const end = new Date(start.getTime() + 24 * 60 * 60 * 1000)
  return { from: start.toISOString(), to: end.toISOString() }
}

export const useProfileStore = defineStore('profile', () => {
  const activeUserId = ref<string | null>(null)
  const quota = ref<QuotaResponse | null>(null)
  const usage = ref<UsageSummary | null>(null)
  const feedback = ref<FeedbackPage>(emptyFeedback())
  const resetAt = ref<string | null>(null)
  const loading = ref(false)
  const feedbackLoading = ref(false)
  const error = ref<unknown>(null)
  let generation = 0

  function reset(): void {
    generation += 1
    activeUserId.value = null
    quota.value = null
    usage.value = null
    feedback.value = emptyFeedback()
    resetAt.value = null
    loading.value = false
    feedbackLoading.value = false
    error.value = null
  }

  async function load(userId: string, now = new Date()): Promise<void> {
    const requestGeneration = ++generation
    activeUserId.value = userId
    loading.value = true
    error.value = null
    const range = shanghaiDayRange(now)
    try {
      const [nextQuota, nextUsage, nextFeedback] = await Promise.all([
        getMyQuota(),
        getMyUsage(range.from, range.to),
        listMyFeedback({ page: 1, pageSize: feedbackPageSize }),
      ])
      if (requestGeneration !== generation || activeUserId.value !== userId) return
      quota.value = nextQuota
      usage.value = nextUsage
      feedback.value = nextFeedback
      resetAt.value = range.to
    } catch (loadError) {
      if (requestGeneration === generation && activeUserId.value === userId) {
        error.value = loadError
      }
      throw loadError
    } finally {
      if (requestGeneration === generation) loading.value = false
    }
  }

  async function loadFeedback(page: number): Promise<void> {
    const userId = activeUserId.value
    if (!userId) return
    const requestGeneration = generation
    feedbackLoading.value = true
    try {
      const nextFeedback = await listMyFeedback({ page, pageSize: feedbackPageSize })
      if (requestGeneration === generation && activeUserId.value === userId) {
        feedback.value = nextFeedback
      }
    } finally {
      if (requestGeneration === generation) feedbackLoading.value = false
    }
  }

  async function deleteFeedback(messageId: string): Promise<void> {
    const userId = activeUserId.value
    if (!userId) return
    const requestGeneration = generation
    await removeMessageFeedback(messageId)
    if (requestGeneration !== generation || activeUserId.value !== userId) return
    feedback.value = {
      ...feedback.value,
      items: feedback.value.items.filter((item) => item.message_id !== messageId),
      total: Math.max(0, feedback.value.total - 1),
    }
  }

  return {
    activeUserId, quota, usage, feedback, resetAt, loading, feedbackLoading, error,
    load, loadFeedback, deleteFeedback, reset,
  }
})

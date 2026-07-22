import { ref } from 'vue'
import { defineStore } from 'pinia'
import {
  getOperationsJobs,
  getOperationsOverview,
  getOperationsQuality,
  listUserOperations,
  type OperationsTimeRange,
} from '../api/adminOperations'
import type {
  OperationsJobsResponse,
  OperationsOverview,
  OperationsQuality,
  UserOperationsSummary,
} from '../types/api'

const emptyJobs = (): OperationsJobsResponse => ({ items: [], next_cursor: null })

export const useAdminOperationsStore = defineStore('admin-operations', () => {
  const range = ref<OperationsTimeRange>({})
  const overview = ref<OperationsOverview | null>(null)
  const users = ref<UserOperationsSummary[]>([])
  const jobs = ref<OperationsJobsResponse>(emptyJobs())
  const quality = ref<OperationsQuality | null>(null)
  const loading = ref(false)
  const loadingMoreJobs = ref(false)
  const error = ref<unknown>(null)
  let generation = 0

  async function load(nextRange: OperationsTimeRange): Promise<void> {
    const requestGeneration = ++generation
    range.value = { ...nextRange }
    loading.value = true
    error.value = null
    try {
      const [nextOverview, nextUsers, nextJobs, nextQuality] = await Promise.all([
        getOperationsOverview(nextRange),
        listUserOperations(nextRange),
        getOperationsJobs({ ...nextRange, limit: 20 }),
        getOperationsQuality(nextRange),
      ])
      if (requestGeneration !== generation) return
      overview.value = nextOverview
      users.value = nextUsers
      jobs.value = nextJobs
      quality.value = nextQuality
    } catch (loadError) {
      if (requestGeneration === generation) error.value = loadError
      throw loadError
    } finally {
      if (requestGeneration === generation) loading.value = false
    }
  }

  async function loadMoreJobs(): Promise<void> {
    const cursor = jobs.value.next_cursor
    if (!cursor || loadingMoreJobs.value) return
    const requestGeneration = generation
    loadingMoreJobs.value = true
    try {
      const nextPage = await getOperationsJobs({
        ...range.value,
        limit: 20,
        cursorCreatedAt: cursor.created_at,
        cursorId: cursor.id,
      })
      if (requestGeneration !== generation) return
      jobs.value = {
        items: [...jobs.value.items, ...nextPage.items],
        next_cursor: nextPage.next_cursor,
      }
    } finally {
      if (requestGeneration === generation) loadingMoreJobs.value = false
    }
  }

  function reset(): void {
    generation += 1
    range.value = {}
    overview.value = null
    users.value = []
    jobs.value = emptyJobs()
    quality.value = null
    loading.value = false
    loadingMoreJobs.value = false
    error.value = null
  }

  return {
    range, overview, users, jobs, quality, loading, loadingMoreJobs, error,
    load, loadMoreJobs, reset,
  }
})

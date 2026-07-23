import { ref } from 'vue'
import { defineStore } from 'pinia'
import {
  createAdminUser,
  getAdminUserQuota,
  listAdminUsers,
  resetAdminUserPassword,
  updateAdminUserQuota,
  updateAdminUser,
  type AdminUserCreateInput,
  type AdminUserUpdateInput,
  type AdminQuotaInput,
} from '../api/adminUsers'
import type { AdminQuota, AdminUser } from '../types/api'

export const useAdminUsersStore = defineStore('admin-users', () => {
  const users = ref<AdminUser[]>([])
  const loading = ref(false)
  const error = ref<unknown>(null)
  const quotas = ref<Record<string, AdminQuota>>({})
  let operationTail: Promise<void> = Promise.resolve()
  let queuedLoadCount = 0

  function enqueue<T>(operation: () => Promise<T>): Promise<T> {
    const result = operationTail.then(operation, operation)
    operationTail = result.then(() => undefined, () => undefined)
    return result
  }

  function replaceUser(updated: AdminUser): void {
    const index = users.value.findIndex((user) => user.id === updated.id)
    if (index >= 0) users.value.splice(index, 1, updated)
  }

  function loadUsers(): Promise<void> {
    queuedLoadCount += 1
    loading.value = true
    return enqueue(async () => {
      error.value = null
      try {
        users.value = await listAdminUsers()
      } catch (loadError) {
        error.value = loadError
        throw loadError
      } finally {
        queuedLoadCount -= 1
        loading.value = queuedLoadCount > 0
      }
    })
  }

  function createUser(input: AdminUserCreateInput): Promise<AdminUser> {
    return enqueue(async () => {
      const created = await createAdminUser(input)
      users.value.push(created)
      users.value.sort((left, right) => left.username.localeCompare(right.username))
      return created
    })
  }

  function updateUser(
    userId: string,
    input: AdminUserUpdateInput,
  ): Promise<AdminUser> {
    return enqueue(async () => {
      const updated = await updateAdminUser(userId, input)
      replaceUser(updated)
      return updated
    })
  }

  function resetPassword(userId: string, password: string): Promise<AdminUser> {
    return enqueue(async () => {
      const updated = await resetAdminUserPassword(userId, password)
      replaceUser(updated)
      return updated
    })
  }

  function loadQuota(userId: string): Promise<AdminQuota> {
    return enqueue(async () => {
      const quota = await getAdminUserQuota(userId)
      quotas.value = { ...quotas.value, [userId]: quota }
      return quota
    })
  }

  function updateQuota(userId: string, input: AdminQuotaInput): Promise<AdminQuota> {
    return enqueue(async () => {
      const quota = await updateAdminUserQuota(userId, input)
      quotas.value = { ...quotas.value, [userId]: quota }
      return quota
    })
  }

  function reset(): void {
    users.value = []
    quotas.value = {}
    loading.value = false
    error.value = null
  }

  return {
    users, quotas, loading, error,
    loadUsers, createUser, updateUser, resetPassword, loadQuota, updateQuota, reset,
  }
})

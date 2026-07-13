import { ref } from 'vue'
import { defineStore } from 'pinia'
import {
  createAdminUser,
  listAdminUsers,
  resetAdminUserPassword,
  updateAdminUser,
  type AdminUserCreateInput,
  type AdminUserUpdateInput,
} from '../api/adminUsers'
import type { AdminUser } from '../types/api'

export const useAdminUsersStore = defineStore('admin-users', () => {
  const users = ref<AdminUser[]>([])
  const loading = ref(false)
  const error = ref<unknown>(null)

  function replaceUser(updated: AdminUser): void {
    const index = users.value.findIndex((user) => user.id === updated.id)
    if (index >= 0) users.value.splice(index, 1, updated)
  }

  async function loadUsers(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      users.value = await listAdminUsers()
    } catch (loadError) {
      error.value = loadError
      throw loadError
    } finally {
      loading.value = false
    }
  }

  async function createUser(input: AdminUserCreateInput): Promise<AdminUser> {
    const created = await createAdminUser(input)
    users.value.push(created)
    users.value.sort((left, right) => left.username.localeCompare(right.username))
    return created
  }

  async function updateUser(
    userId: string,
    input: AdminUserUpdateInput,
  ): Promise<AdminUser> {
    const updated = await updateAdminUser(userId, input)
    replaceUser(updated)
    return updated
  }

  async function resetPassword(userId: string, password: string): Promise<AdminUser> {
    const updated = await resetAdminUserPassword(userId, password)
    replaceUser(updated)
    return updated
  }

  return { users, loading, error, loadUsers, createUser, updateUser, resetPassword }
})

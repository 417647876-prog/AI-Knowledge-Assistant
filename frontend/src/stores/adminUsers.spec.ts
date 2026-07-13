import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AdminUser } from '../types/api'

vi.mock('../api/adminUsers', () => ({
  listAdminUsers: vi.fn(),
  createAdminUser: vi.fn(),
  updateAdminUser: vi.fn(),
  resetAdminUserPassword: vi.fn(),
}))

import {
  createAdminUser,
  listAdminUsers,
  resetAdminUserPassword,
  updateAdminUser,
} from '../api/adminUsers'
import { useAdminUsersStore } from './adminUsers'

const alice: AdminUser = {
  id: 'u-1', username: 'alice', role: 'user', is_active: true,
  created_at: '2026-07-13T08:00:00Z', updated_at: '2026-07-13T08:00:00Z',
}

describe('admin users store', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('加载用户列表并维护 loading 与错误状态', async () => {
    vi.mocked(listAdminUsers).mockResolvedValue([alice])
    const store = useAdminUsersStore()

    await store.loadUsers()

    expect(store.users).toEqual([alice])
    expect(store.loading).toBe(false)
    expect(store.error).toBeNull()
  })

  it('列表加载失败后保留原列表并恢复 loading、记录错误', async () => {
    const failure = new Error('load failed')
    let rejectLoad!: (error: Error) => void
    vi.mocked(listAdminUsers).mockReturnValue(new Promise((_resolve, reject) => {
      rejectLoad = reject
    }))
    const store = useAdminUsersStore()
    store.users = [alice]

    const loading = store.loadUsers()
    expect(store.loading).toBe(true)
    rejectLoad(failure)
    await expect(loading).rejects.toBe(failure)

    expect(store.users).toEqual([alice])
    expect(store.loading).toBe(false)
    expect(store.error).toBe(failure)
  })

  it('创建成功后把新用户加入当前列表且不重复拉取', async () => {
    const bob = { ...alice, id: 'u-2', username: 'bob' }
    vi.mocked(createAdminUser).mockResolvedValue(bob)
    const store = useAdminUsersStore()
    store.users = [alice]

    await store.createUser({ username: 'bob', password: 'temporary pass 123', role: 'user' })

    expect(store.users).toEqual([alice, bob])
    expect(listAdminUsers).not.toHaveBeenCalled()
  })

  it('创建成功后维持与后端列表一致的用户名排序', async () => {
    const aaron = { ...alice, id: 'u-2', username: 'aaron' }
    vi.mocked(createAdminUser).mockResolvedValue(aaron)
    const store = useAdminUsersStore()
    store.users = [alice]

    await store.createUser({ username: 'aaron', password: 'temporary pass 123', role: 'user' })

    expect(store.users.map((user) => user.username)).toEqual(['aaron', 'alice'])
  })

  it('mutation API 拒绝后列表保持不变', async () => {
    const failure = new Error('create failed')
    vi.mocked(createAdminUser).mockRejectedValue(failure)
    const store = useAdminUsersStore()
    store.users = [alice]

    await expect(store.createUser({
      username: 'bob', password: 'temporary pass 123', role: 'user',
    })).rejects.toBe(failure)

    expect(store.users).toEqual([alice])
  })

  it('修改成功后只替换目标用户且保留列表其他项', async () => {
    const bob = { ...alice, id: 'u-2', username: 'bob' }
    const updatedAlice = { ...alice, role: 'admin' as const }
    vi.mocked(updateAdminUser).mockResolvedValue(updatedAlice)
    const store = useAdminUsersStore()
    store.users = [alice, bob]

    await store.updateUser('u-1', { role: 'admin' })

    expect(store.users).toEqual([updatedAlice, bob])
    expect(listAdminUsers).not.toHaveBeenCalled()
  })

  it('重置密码成功后更新目标用户且不保存明文密码', async () => {
    const resetUser = { ...alice, updated_at: '2026-07-13T09:00:00Z' }
    vi.mocked(resetAdminUserPassword).mockResolvedValue(resetUser)
    const store = useAdminUsersStore()
    store.users = [alice]

    await store.resetPassword('u-1', 'replacement pass 123')

    expect(store.users).toEqual([resetUser])
    expect(store).not.toHaveProperty('password')
    expect(JSON.stringify(store.$state)).not.toContain('replacement pass 123')
  })
})

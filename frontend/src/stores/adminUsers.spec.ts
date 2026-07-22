import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AdminUser } from '../types/api'

vi.mock('../api/adminUsers', () => ({
  listAdminUsers: vi.fn(),
  createAdminUser: vi.fn(),
  getAdminUserQuota: vi.fn(),
  updateAdminUser: vi.fn(),
  updateAdminUserQuota: vi.fn(),
  resetAdminUserPassword: vi.fn(),
}))

import {
  createAdminUser,
  getAdminUserQuota,
  listAdminUsers,
  resetAdminUserPassword,
  updateAdminUser,
  updateAdminUserQuota,
} from '../api/adminUsers'
import { useAdminUsersStore } from './adminUsers'

const alice: AdminUser = {
  id: 'u-1', username: 'alice', role: 'user', is_active: true,
  created_at: '2026-07-13T08:00:00Z', updated_at: '2026-07-13T08:00:00Z',
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
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

  it('GET pending 时后调用的创建等待旧 GET，创建结果最终保留', async () => {
    const loadRequest = deferred<AdminUser[]>()
    const createRequest = deferred<AdminUser>()
    const bob = { ...alice, id: 'u-2', username: 'bob' }
    vi.mocked(listAdminUsers).mockReturnValue(loadRequest.promise)
    vi.mocked(createAdminUser).mockReturnValue(createRequest.promise)
    const store = useAdminUsersStore()

    const loading = store.loadUsers()
    const creating = store.createUser({
      username: 'bob', password: 'temporary pass 123', role: 'user',
    })

    await vi.waitFor(() => expect(listAdminUsers).toHaveBeenCalledOnce())
    expect(createAdminUser).not.toHaveBeenCalled()
    loadRequest.resolve([alice])
    await loading
    await vi.waitFor(() => expect(createAdminUser).toHaveBeenCalledOnce())
    createRequest.resolve(bob)
    await creating

    expect(store.users).toEqual([alice, bob])
  })

  it('重叠 load 按调用顺序执行且不会提前解除 loading 或越过 mutation', async () => {
    const firstLoad = deferred<AdminUser[]>()
    const secondLoad = deferred<AdminUser[]>()
    const updateRequest = deferred<AdminUser>()
    const updatedAlice = { ...alice, role: 'admin' as const }
    vi.mocked(listAdminUsers)
      .mockReturnValueOnce(firstLoad.promise)
      .mockReturnValueOnce(secondLoad.promise)
    vi.mocked(updateAdminUser).mockReturnValue(updateRequest.promise)
    const store = useAdminUsersStore()

    const first = store.loadUsers()
    const second = store.loadUsers()
    const updating = store.updateUser('u-1', { role: 'admin' })

    await vi.waitFor(() => expect(listAdminUsers).toHaveBeenCalledOnce())
    expect(updateAdminUser).not.toHaveBeenCalled()
    expect(store.loading).toBe(true)

    firstLoad.resolve([alice])
    await first
    await vi.waitFor(() => expect(listAdminUsers).toHaveBeenCalledTimes(2))
    expect(store.loading).toBe(true)
    expect(updateAdminUser).not.toHaveBeenCalled()

    secondLoad.resolve([alice])
    await second
    await vi.waitFor(() => expect(updateAdminUser).toHaveBeenCalledOnce())
    expect(store.loading).toBe(false)
    updateRequest.resolve(updatedAlice)
    await updating

    expect(store.users).toEqual([updatedAlice])
  })

  it('mutation pending 时后调用的 load 等待 mutation，不能用旧结果覆盖', async () => {
    const updateRequest = deferred<AdminUser>()
    const loadRequest = deferred<AdminUser[]>()
    const updatedAlice = { ...alice, is_active: false }
    vi.mocked(updateAdminUser).mockReturnValue(updateRequest.promise)
    vi.mocked(listAdminUsers).mockReturnValue(loadRequest.promise)
    const store = useAdminUsersStore()
    store.users = [alice]

    const updating = store.updateUser('u-1', { is_active: false })
    const loading = store.loadUsers()

    await vi.waitFor(() => expect(updateAdminUser).toHaveBeenCalledOnce())
    expect(listAdminUsers).not.toHaveBeenCalled()
    updateRequest.resolve(updatedAlice)
    await updating
    await vi.waitFor(() => expect(listAdminUsers).toHaveBeenCalledOnce())
    loadRequest.resolve([updatedAlice])
    await loading

    expect(store.users).toEqual([updatedAlice])
  })

  it('排队操作失败后释放队列，后续 mutation 仍可完成', async () => {
    const loadRequest = deferred<AdminUser[]>()
    const createRequest = deferred<AdminUser>()
    const failure = new Error('load failed')
    const bob = { ...alice, id: 'u-2', username: 'bob' }
    vi.mocked(listAdminUsers)
      .mockReturnValueOnce(loadRequest.promise)
      .mockResolvedValueOnce([bob])
    vi.mocked(createAdminUser).mockReturnValue(createRequest.promise)
    const store = useAdminUsersStore()

    const loading = store.loadUsers()
    const loadFailure = loading.catch((error: unknown) => error)
    const creating = store.createUser({
      username: 'bob', password: 'temporary pass 123', role: 'user',
    })
    await vi.waitFor(() => expect(listAdminUsers).toHaveBeenCalledOnce())
    expect(createAdminUser).not.toHaveBeenCalled()

    loadRequest.reject(failure)
    expect(await loadFailure).toBe(failure)
    await vi.waitFor(() => expect(createAdminUser).toHaveBeenCalledOnce())
    createRequest.resolve(bob)
    await creating

    expect(store.loading).toBe(false)
    expect(store.users).toEqual([bob])

    await store.loadUsers()
    expect(listAdminUsers).toHaveBeenCalledTimes(2)
    expect(store.users).toEqual([bob])
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

  it('按用户加载并调整额度覆盖值', async () => {
    const quota = {
      daily_question_limit: 30,
      daily_upload_limit: null,
      storage_bytes_limit: 1073741824,
    }
    vi.mocked(getAdminUserQuota).mockResolvedValue(quota)
    vi.mocked(updateAdminUserQuota).mockResolvedValue({ ...quota, daily_upload_limit: 8 })
    const store = useAdminUsersStore()

    await store.loadQuota('u-1')
    await store.updateQuota('u-1', { ...quota, daily_upload_limit: 8 })

    expect(store.quotas['u-1']).toEqual({ ...quota, daily_upload_limit: 8 })
  })
})

import { afterEach, describe, expect, it, vi } from 'vitest'
import { configureAuthentication } from './client'
import {
  createAdminUser,
  getAdminUserQuota,
  listAdminUsers,
  resetAdminUserPassword,
  updateAdminUserQuota,
  updateAdminUser,
} from './adminUsers'

afterEach(() => vi.unstubAllGlobals())

const user = {
  id: 'u-1', username: 'alice', role: 'user' as const, is_active: true,
  created_at: '2026-07-13T08:00:00Z', updated_at: '2026-07-13T08:00:00Z',
}

describe('admin users API', () => {
  it('使用管理员用户列表接口并携带 Bearer', async () => {
    configureAuthentication({
      getAccessToken: () => 'admin-token',
      refreshAccessToken: vi.fn(),
      onAuthenticationFailed: vi.fn(),
    })
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify([user]), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(listAdminUsers()).resolves.toEqual([user])

    expect(fetchMock).toHaveBeenCalledOnce()
    const [path, init] = fetchMock.mock.calls[0]!
    expect(path).toBe('/api/v1/admin/users')
    expect((init.headers as Headers).get('Authorization')).toBe('Bearer admin-token')
  })

  it('创建用户时只发送后端支持的固定字段', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(user), {
      status: 201, headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await createAdminUser({ username: 'alice', password: 'temporary pass 123', role: 'user' })

    const [path, init] = fetchMock.mock.calls[0]!
    expect(path).toBe('/api/v1/admin/users')
    expect(init).toMatchObject({ method: 'POST' })
    expect(JSON.parse(init.body as string)).toEqual({
      username: 'alice', password: 'temporary pass 123', role: 'user',
    })
  })

  it('修改与重置密码使用后端现有接口且不返回密码字段', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...user, role: 'admin' }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify(user), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      }))
    vi.stubGlobal('fetch', fetchMock)

    const updated = await updateAdminUser('u-1', { role: 'admin', is_active: true })
    const reset = await resetAdminUserPassword('u-1', 'replacement pass 123')

    expect(fetchMock.mock.calls[0]![0]).toBe('/api/v1/admin/users/u-1')
    expect(fetchMock.mock.calls[0]![1]).toMatchObject({ method: 'PATCH' })
    expect(JSON.parse(fetchMock.mock.calls[0]![1].body as string)).toEqual({
      role: 'admin', is_active: true,
    })
    expect(fetchMock.mock.calls[1]![0]).toBe('/api/v1/admin/users/u-1/reset-password')
    expect(fetchMock.mock.calls[1]![1]).toMatchObject({ method: 'POST' })
    expect(JSON.parse(fetchMock.mock.calls[1]![1].body as string)).toEqual({
      password: 'replacement pass 123',
    })
    expect(updated).not.toHaveProperty('password')
    expect(reset).not.toHaveProperty('password')
  })

  it.each([403, 404, 409])('原样传递 %i 错误信封', async (status) => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: {
        code: `ADMIN_ERROR_${status}`,
        message: `管理员请求失败 ${status}。`,
        request_id: `req-${status}`,
      },
    }), { status, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(listAdminUsers()).rejects.toMatchObject({
      status,
      code: `ADMIN_ERROR_${status}`,
      message: `管理员请求失败 ${status}。`,
      requestId: `req-${status}`,
    })
  })

  it('查看和调整用户额度时只发送三个可覆盖字段', async () => {
    const quota = {
      daily_question_limit: 30,
      daily_upload_limit: null,
      storage_bytes_limit: 1073741824,
    }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(quota), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify(quota), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      }))
    vi.stubGlobal('fetch', fetchMock)

    await getAdminUserQuota('u-1')
    await updateAdminUserQuota('u-1', quota)

    expect(fetchMock.mock.calls[0]![0]).toBe('/api/v1/admin/users/u-1/quota')
    expect(fetchMock.mock.calls[1]![0]).toBe('/api/v1/admin/users/u-1/quota')
    expect(fetchMock.mock.calls[1]![1]).toMatchObject({ method: 'PUT' })
    expect(JSON.parse(fetchMock.mock.calls[1]![1].body as string)).toEqual(quota)
  })
})

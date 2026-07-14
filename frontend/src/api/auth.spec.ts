import { afterEach, describe, expect, it, vi } from 'vitest'
import { configureAuthentication } from './client'
import { login, logout, refresh } from './auth'

afterEach(() => vi.unstubAllGlobals())

describe('auth API', () => {
  it('logs in with the refresh cookie enabled and without bearer authentication', async () => {
    const refreshAccessToken = vi.fn()
    configureAuthentication({
      getAccessToken: () => 'old-token',
      refreshAccessToken,
      onAuthenticationFailed: vi.fn(),
    })
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      access_token: 'new-token', token_type: 'bearer', expires_in: 900,
      user: { id: 'u-1', username: 'alice', role: 'user', is_active: true },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    await login('alice', 'secret')

    expect(fetchMock).toHaveBeenCalledOnce()
    const [path, init] = fetchMock.mock.calls[0]!
    expect(path).toBe('/api/v1/auth/login')
    expect(init).toMatchObject({ method: 'POST', credentials: 'include' })
    expect((init.headers as Headers).get('Authorization')).toBeNull()
    expect(JSON.parse(init.body as string)).toEqual({ username: 'alice', password: 'secret' })
    expect(refreshAccessToken).not.toHaveBeenCalled()
  })

  it('refreshes with the cookie enabled and does not recursively retry a 401', async () => {
    const refreshAccessToken = vi.fn()
    configureAuthentication({
      getAccessToken: () => 'expired-token',
      refreshAccessToken,
      onAuthenticationFailed: vi.fn(),
    })
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 401 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(refresh()).rejects.toMatchObject({ status: 401 })

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0]![0]).toBe('/api/v1/auth/refresh')
    expect(fetchMock.mock.calls[0]![1]).toMatchObject({ method: 'POST', credentials: 'include' })
    expect(refreshAccessToken).not.toHaveBeenCalled()
  })

  it('logs out with the cookie enabled and accepts an empty 204 response', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(logout()).resolves.toBeUndefined()

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0]![0]).toBe('/api/v1/auth/logout')
    expect(fetchMock.mock.calls[0]![1]).toMatchObject({ method: 'POST', credentials: 'include' })
  })
})

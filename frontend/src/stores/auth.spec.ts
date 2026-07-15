import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AuthSession } from '../types/api'

vi.mock('../api/auth', () => ({
  login: vi.fn(), refresh: vi.fn(), logout: vi.fn(),
}))

import { login, logout, refresh } from '../api/auth'
import { apiRequest } from '../api/client'
import { useAuthStore } from './auth'
import { useConversationsStore } from './conversations'
import { useWorkspaceStore } from './workspace'

const userSession: AuthSession = {
  access_token: 'access-token', token_type: 'bearer', expires_in: 900,
  user: { id: 'u-1', username: 'alice', role: 'user', is_active: true },
}

describe('auth store', () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => vi.unstubAllGlobals())

  it('restores the in-memory session during initialization', async () => {
    vi.mocked(refresh).mockResolvedValue(userSession)
    const store = useAuthStore()

    await store.initialize()

    expect(store.accessToken).toBe('access-token')
    expect(store.user).toEqual(userSession.user)
    expect(store.initialized).toBe(true)
    expect(store.initializing).toBe(false)
    expect(store.isAdmin).toBe(false)
  })

  it('shares initialization so concurrent callers wait for the same refresh', async () => {
    let resolveRefresh!: (session: AuthSession) => void
    vi.mocked(refresh).mockReturnValue(new Promise((resolve) => { resolveRefresh = resolve }))
    const store = useAuthStore()

    const first = store.initialize()
    let secondFinished = false
    const second = store.initialize().then(() => { secondFinished = true })
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(secondFinished).toBe(false)

    resolveRefresh(userSession)
    await Promise.all([first, second])
    expect(refresh).toHaveBeenCalledOnce()
    expect(store.user).toEqual(userSession.user)
  })

  it('keeps the access token and user in memory after login', async () => {
    const adminSession: AuthSession = {
      ...userSession,
      access_token: 'admin-token',
      user: { ...userSession.user, role: 'admin' },
    }
    vi.mocked(login).mockResolvedValue(adminSession)
    const store = useAuthStore()

    await store.login('admin', 'secret')

    expect(login).toHaveBeenCalledWith('admin', 'secret')
    expect(store.accessToken).toBe('admin-token')
    expect(store.user).toEqual(adminSession.user)
    expect(store.isAdmin).toBe(true)
  })

  it('waits for an old refresh before applying a newly logged-in session', async () => {
    let resolveRefresh!: (session: AuthSession) => void
    vi.mocked(refresh).mockReturnValue(new Promise((resolve) => { resolveRefresh = resolve }))
    const newSession: AuthSession = {
      ...userSession,
      access_token: 'new-user-token',
      user: { ...userSession.user, id: 'u-2', username: 'bob' },
    }
    vi.mocked(login).mockResolvedValue(newSession)
    const store = useAuthStore()

    const initializing = store.initialize()
    const loggingIn = store.login('bob', 'secret')
    expect(login).not.toHaveBeenCalled()

    resolveRefresh(userSession)
    await Promise.all([initializing, loggingIn])

    expect(login).toHaveBeenCalledOnce()
    expect(store.accessToken).toBe('new-user-token')
    expect(store.user).toEqual(newSession.user)
  })

  it('does not let initialization refresh overwrite a login that started first', async () => {
    let resolveLogin!: (session: AuthSession) => void
    let resolveRefresh: ((session: AuthSession) => void) | undefined
    const bobSession: AuthSession = {
      ...userSession,
      access_token: 'bob-token',
      user: { ...userSession.user, id: 'u-bob', username: 'bob' },
    }
    vi.mocked(login).mockReturnValue(new Promise((resolve) => { resolveLogin = resolve }))
    vi.mocked(refresh).mockReturnValue(new Promise((resolve) => { resolveRefresh = resolve }))
    const store = useAuthStore()

    const loggingIn = store.login('bob', 'secret')
    const initializing = store.initialize()
    resolveLogin(bobSession)
    await loggingIn
    resolveRefresh?.(userSession)
    await initializing

    expect(refresh).not.toHaveBeenCalled()
    expect(store.accessToken).toBe('bob-token')
    expect(store.user).toEqual(bobSession.user)
  })

  it('stays anonymous when a login observed by initialization fails', async () => {
    let rejectLogin!: (error: Error) => void
    vi.mocked(login).mockReturnValue(new Promise((_resolve, reject) => { rejectLogin = reject }))
    vi.mocked(refresh).mockResolvedValue(userSession)
    const store = useAuthStore()

    const loggingIn = store.login('bob', 'wrong-secret')
    const loginResult = expect(loggingIn).rejects.toThrow('login failed')
    const initializing = store.initialize()
    rejectLogin(new Error('login failed'))
    await Promise.all([loginResult, initializing])

    expect(refresh).not.toHaveBeenCalled()
    expect(store.accessToken).toBeNull()
    expect(store.user).toBeNull()
  })

  it('does not start an automatic refresh while a login is changing the session', async () => {
    let resolveLogin!: (session: AuthSession) => void
    const bobSession: AuthSession = {
      ...userSession,
      access_token: 'bob-token',
      user: { ...userSession.user, id: 'u-bob', username: 'bob' },
    }
    vi.mocked(login).mockReturnValue(new Promise((resolve) => { resolveLogin = resolve }))
    vi.mocked(refresh).mockResolvedValue(userSession)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 401 })))
    const store = useAuthStore()

    const loggingIn = store.login('bob', 'secret')
    await expect(apiRequest('/protected')).rejects.toMatchObject({ status: 401 })
    resolveLogin(bobSession)
    await loggingIn

    expect(refresh).not.toHaveBeenCalled()
    expect(store.accessToken).toBe('bob-token')
    expect(store.user).toEqual(bobSession.user)
  })

  it('serializes overlapping logins so logout waits for every cookie response', async () => {
    let resolveFirstLogin!: (session: AuthSession) => void
    vi.mocked(login).mockReturnValueOnce(new Promise((resolve) => { resolveFirstLogin = resolve }))
    vi.mocked(logout).mockResolvedValue(undefined)
    const store = useAuthStore()

    const firstLogin = store.login('alice', 'first-secret')
    const secondLogin = store.login('bob', 'second-secret')
    const secondResult = expect(secondLogin).rejects.toThrow('认证操作已取消。')
    expect(login).toHaveBeenCalledOnce()

    const loggingOut = store.logout()
    expect(logout).not.toHaveBeenCalled()
    resolveFirstLogin(userSession)
    await expect(firstLogin).resolves.toEqual(userSession)
    await secondResult
    await loggingOut

    expect(login).toHaveBeenCalledOnce()
    expect(logout).toHaveBeenCalledOnce()
    expect(store.accessToken).toBeNull()
    expect(store.user).toBeNull()
  })

  it('clears auth and workspace state even when the logout request fails', async () => {
    vi.mocked(login).mockResolvedValue(userSession)
    const apiError = new Error('offline')
    vi.mocked(logout).mockRejectedValue(apiError)
    const store = useAuthStore()
    const workspace = useWorkspaceStore()
    const clearUser = vi.spyOn(useConversationsStore(), 'clearUser')
    await store.login('alice', 'secret')
    workspace.knowledgeBases = [{
      id: 'kb-1', name: '制度', description: null,
      owner_id: 'u-1', owner_username: 'alice',
    }]
    workspace.activeKnowledgeBaseId = 'kb-1'

    await expect(store.logout()).rejects.toBe(apiError)

    expect(store.accessToken).toBeNull()
    expect(store.user).toBeNull()
    expect(workspace.knowledgeBases).toEqual([])
    expect(workspace.activeKnowledgeBaseId).toBeNull()
    expect(clearUser).toHaveBeenCalledWith('u-1')
  })

  it('认证刷新失败后清理当前用户的会话历史', async () => {
    vi.mocked(login).mockResolvedValue(userSession)
    vi.mocked(refresh).mockRejectedValue(new Error('refresh failed'))
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 401 })))
    const store = useAuthStore()
    const clearUser = vi.spyOn(useConversationsStore(), 'clearUser')
    await store.login('alice', 'secret')

    await expect(apiRequest('/protected')).rejects.toMatchObject({ status: 401 })

    expect(clearUser).toHaveBeenCalledWith('u-1')
    expect(store.user).toBeNull()
  })

  it('invalidates an in-flight refresh before logging out on the server', async () => {
    vi.mocked(login).mockResolvedValue(userSession)
    vi.mocked(logout).mockResolvedValue(undefined)
    let resolveRefresh!: (session: AuthSession) => void
    vi.mocked(refresh).mockReturnValue(new Promise((resolve) => { resolveRefresh = resolve }))
    const store = useAuthStore()
    await store.login('alice', 'secret')
    store.accessToken = null
    store.user = null
    const initializing = store.initialize()

    const loggingOut = store.logout()
    expect(store.accessToken).toBeNull()
    expect(store.user).toBeNull()
    expect(logout).not.toHaveBeenCalled()

    resolveRefresh({ ...userSession, access_token: 'rotated-token' })
    await Promise.all([initializing, loggingOut])

    expect(logout).toHaveBeenCalledOnce()
    expect(store.accessToken).toBeNull()
    expect(store.user).toBeNull()
  })

  it('waits for logout before sending a login that starts later', async () => {
    vi.mocked(login).mockResolvedValue(userSession)
    let resolveLogout!: () => void
    vi.mocked(logout).mockReturnValue(new Promise((resolve) => { resolveLogout = resolve }))
    const store = useAuthStore()
    await store.login('alice', 'secret')
    vi.mocked(login).mockClear()
    const bobSession: AuthSession = {
      ...userSession,
      access_token: 'bob-token',
      user: { ...userSession.user, id: 'u-bob', username: 'bob' },
    }
    vi.mocked(login).mockResolvedValue(bobSession)

    const loggingOut = store.logout()
    const loggingIn = store.login('bob', 'secret')
    expect(login).not.toHaveBeenCalled()

    resolveLogout()
    await Promise.all([loggingOut, loggingIn])

    expect(login).toHaveBeenCalledWith('bob', 'secret')
    expect(store.accessToken).toBe('bob-token')
    expect(store.user).toEqual(bobSession.user)
  })

  it('stays anonymous when a logout observed by initialization fails', async () => {
    vi.mocked(login).mockResolvedValue(userSession)
    let rejectLogout!: (error: Error) => void
    vi.mocked(logout).mockReturnValue(new Promise((_resolve, reject) => { rejectLogout = reject }))
    vi.mocked(refresh).mockResolvedValue(userSession)
    const store = useAuthStore()
    await store.login('alice', 'secret')

    const loggingOut = store.logout()
    const logoutResult = expect(loggingOut).rejects.toThrow('logout failed')
    const initializing = store.initialize()
    rejectLogout(new Error('logout failed'))
    await Promise.all([logoutResult, initializing])

    expect(refresh).not.toHaveBeenCalled()
    expect(store.accessToken).toBeNull()
    expect(store.user).toBeNull()
  })

  it('stays anonymous when initialization cannot refresh the session', async () => {
    vi.mocked(refresh).mockRejectedValue(new Error('no refresh cookie'))
    const store = useAuthStore()

    await expect(store.initialize()).resolves.toBeUndefined()

    expect(store.accessToken).toBeNull()
    expect(store.user).toBeNull()
    expect(store.initialized).toBe(true)
    expect(store.initializing).toBe(false)
  })
})

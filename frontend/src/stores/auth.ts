import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import {
  login as loginRequest,
  logout as logoutRequest,
  refresh as refreshSession,
} from '../api/auth'
import { configureAuthentication } from '../api/client'
import type { AuthSession, CurrentUser } from '../types/api'
import { useWorkspaceStore } from './workspace'

export const useAuthStore = defineStore('auth', () => {
  const accessToken = ref<string | null>(null)
  const user = ref<CurrentUser | null>(null)
  const initialized = ref(false)
  const initializing = ref(false)
  const isAdmin = computed(() => user.value?.role === 'admin')
  const workspace = useWorkspaceStore()
  let authenticationGeneration = 0
  let refreshSessionPromise: Promise<AuthSession> | null = null
  let loginSessionPromise: Promise<AuthSession> | null = null
  let initializationPromise: Promise<void> | null = null

  function applySession(session: AuthSession) {
    accessToken.value = session.access_token
    user.value = session.user
  }

  function clearSession() {
    accessToken.value = null
    user.value = null
    workspace.reset()
  }

  function requestRefreshSession(): Promise<AuthSession> {
    if (!refreshSessionPromise) {
      const pending = refreshSession()
      refreshSessionPromise = pending
      pending.then(
        () => { if (refreshSessionPromise === pending) refreshSessionPromise = null },
        () => { if (refreshSessionPromise === pending) refreshSessionPromise = null },
      )
    }
    return refreshSessionPromise
  }

  configureAuthentication({
    getAccessToken: () => accessToken.value,
    refreshAccessToken: async () => {
      const generation = authenticationGeneration
      try {
        const session = await requestRefreshSession()
        if (generation !== authenticationGeneration) return null
        applySession(session)
        return session.access_token
      } catch {
        if (generation === authenticationGeneration) {
          accessToken.value = null
          user.value = null
        }
        return null
      }
    },
    onAuthenticationFailed: clearSession,
  })

  function initialize(): Promise<void> {
    if (initialized.value) return Promise.resolve()
    if (initializationPromise) return initializationPromise
    const generation = authenticationGeneration
    initializing.value = true
    initializationPromise = (async () => {
      try {
        const session = await requestRefreshSession()
        if (generation === authenticationGeneration) applySession(session)
      } catch {
        if (generation === authenticationGeneration) clearSession()
      } finally {
        initialized.value = true
        initializing.value = false
        initializationPromise = null
      }
    })()
    return initializationPromise
  }

  async function login(username: string, password: string) {
    authenticationGeneration += 1
    const generation = authenticationGeneration
    clearSession()
    const pendingRefresh = refreshSessionPromise
    if (pendingRefresh) await pendingRefresh.catch(() => undefined)
    const previousLogin = loginSessionPromise
    if (previousLogin) await previousLogin.catch(() => undefined)
    if (generation !== authenticationGeneration) throw new Error('认证操作已取消。')
    const pendingLogin = loginRequest(username, password)
    loginSessionPromise = pendingLogin
    try {
      const session = await pendingLogin
      if (generation === authenticationGeneration) applySession(session)
      return session
    } finally {
      if (loginSessionPromise === pendingLogin) loginSessionPromise = null
    }
  }

  async function logout() {
    authenticationGeneration += 1
    clearSession()
    const pendingRefresh = refreshSessionPromise
    if (pendingRefresh) await pendingRefresh.catch(() => undefined)
    const pendingLogin = loginSessionPromise
    if (pendingLogin) await pendingLogin.catch(() => undefined)
    try {
      await logoutRequest()
    } finally {
      clearSession()
    }
  }

  return { accessToken, user, initialized, initializing, isAdmin, initialize, login, logout }
})

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import {
  login as loginRequest,
  logout as logoutRequest,
  refresh as refreshSession,
} from '../api/auth'
import { configureAuthentication } from '../api/client'
import type { AuthSession, CurrentUser } from '../types/api'
import { useConversationsStore } from './conversations'
import { useWorkspaceStore } from './workspace'

export const useAuthStore = defineStore('auth', () => {
  const accessToken = ref<string | null>(null)
  const user = ref<CurrentUser | null>(null)
  const initialized = ref(false)
  const initializing = ref(false)
  const isAdmin = computed(() => user.value?.role === 'admin')
  const workspace = useWorkspaceStore()
  const conversations = useConversationsStore()
  let authenticationGeneration = 0
  let refreshSessionPromise: Promise<AuthSession> | null = null
  let loginSessionPromise: Promise<AuthSession> | null = null
  let logoutSessionPromise: Promise<void> | null = null
  let initializationPromise: Promise<void> | null = null

  function applySession(session: AuthSession) {
    accessToken.value = session.access_token
    user.value = session.user
  }

  function clearSession() {
    const userId = user.value?.id
    if (userId) conversations.clearUser(userId)
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
      if (loginSessionPromise || logoutSessionPromise) return null
      const generation = authenticationGeneration
      try {
        const session = await requestRefreshSession()
        if (generation !== authenticationGeneration) return null
        applySession(session)
        return session.access_token
      } catch {
        if (generation === authenticationGeneration) clearSession()
        return null
      }
    },
    onAuthenticationFailed: clearSession,
  })

  function initialize(): Promise<void> {
    if (initialized.value) return Promise.resolve()
    if (initializationPromise) return initializationPromise
    const generation = authenticationGeneration
    const pendingLogout = logoutSessionPromise
    const pendingLogin = loginSessionPromise
    const observedExplicitOperation = Boolean(pendingLogout || pendingLogin)
    initializing.value = true
    initializationPromise = (async () => {
      try {
        if (pendingLogout) await pendingLogout.catch(() => undefined)
        if (pendingLogin) await pendingLogin.catch(() => undefined)
        if (
          observedExplicitOperation
          || generation !== authenticationGeneration
          || accessToken.value
        ) return
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

  function login(username: string, password: string): Promise<AuthSession> {
    authenticationGeneration += 1
    const generation = authenticationGeneration
    clearSession()
    const previousLogin = loginSessionPromise
    const previousLogout = logoutSessionPromise
    const operation = (async () => {
      if (previousLogout) await previousLogout.catch(() => undefined)
      const pendingRefresh = refreshSessionPromise
      if (pendingRefresh) await pendingRefresh.catch(() => undefined)
      if (previousLogin) await previousLogin.catch(() => undefined)
      if (generation !== authenticationGeneration) throw new Error('认证操作已取消。')
      const session = await loginRequest(username, password)
      if (generation === authenticationGeneration) applySession(session)
      return session
    })()
    loginSessionPromise = operation
    operation.then(
      () => { if (loginSessionPromise === operation) loginSessionPromise = null },
      () => { if (loginSessionPromise === operation) loginSessionPromise = null },
    )
    return operation
  }

  function logout(): Promise<void> {
    authenticationGeneration += 1
    clearSession()
    const pendingLogin = loginSessionPromise
    const previousLogout = logoutSessionPromise
    const operation = (async () => {
      if (previousLogout) await previousLogout.catch(() => undefined)
      const pendingRefresh = refreshSessionPromise
      if (pendingRefresh) await pendingRefresh.catch(() => undefined)
      if (pendingLogin) await pendingLogin.catch(() => undefined)
      try {
        await logoutRequest()
      } finally {
        clearSession()
      }
    })()
    logoutSessionPromise = operation
    operation.then(
      () => { if (logoutSessionPromise === operation) logoutSessionPromise = null },
      () => { if (logoutSessionPromise === operation) logoutSessionPromise = null },
    )
    return operation
  }

  return { accessToken, user, initialized, initializing, isAdmin, initialize, login, logout }
})

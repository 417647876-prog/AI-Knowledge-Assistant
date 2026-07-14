import type { ApiErrorEnvelope } from '../types/api'

export interface AuthenticationCallbacks {
  getAccessToken: () => string | null
  refreshAccessToken: () => Promise<string | null>
  onAuthenticationFailed: () => void
}

let authentication: AuthenticationCallbacks | null = null
let refreshPromise: Promise<string | null> | null = null
let refreshFromToken: string | null = null
let refreshTransition: { from: string | null; to: string } | null = null

export function configureAuthentication(callbacks: AuthenticationCallbacks): void {
  authentication = callbacks
  refreshTransition = null
}

function notifyAuthenticationFailed(callbacks: AuthenticationCallbacks): void {
  try { callbacks.onAuthenticationFailed() } catch { /* 通知异常不能覆盖原认证错误。 */ }
}

function refreshAccessToken(previousAccessToken: string | null): Promise<string | null> {
  if (!authentication) return Promise.resolve(null)
  if (!refreshPromise) {
    const callbacks = authentication
    refreshFromToken = previousAccessToken
    refreshPromise = (async () => {
      let accessToken: string | null
      try {
        accessToken = await callbacks.refreshAccessToken()
      } catch (error) {
        refreshTransition = null
        notifyAuthenticationFailed(callbacks)
        throw error
      }
      if (accessToken) refreshTransition = { from: previousAccessToken, to: accessToken }
      else {
        refreshTransition = null
        notifyAuthenticationFailed(callbacks)
      }
      return accessToken
    })().finally(() => {
      refreshPromise = null
      refreshFromToken = null
    })
  }
  return refreshPromise
}

export class ApiError extends Error {
  public readonly status: number
  public readonly code: string
  public readonly requestId?: string

  constructor(
    status: number, code: string, message: string, requestId?: string,
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.requestId = requestId
  }
}

export async function apiRequest<T>(
  path: string,
  init?: RequestInit,
  options: { authenticated?: boolean; retryUnauthorized?: boolean } = {},
): Promise<T> {
  const headers = new Headers(init?.headers)
  let requestAccessToken: string | null = null
  if (options.authenticated !== false) {
    requestAccessToken = authentication?.getAccessToken() ?? null
    if (requestAccessToken) headers.set('Authorization', `Bearer ${requestAccessToken}`)
  }
  const requestInit = { ...init, headers }
  let response: Response
  try { response = await fetch(path, requestInit) }
  catch { throw new ApiError(0, 'NETWORK_ERROR', '服务暂不可用，请稍后重试。') }
  if (
    response.status === 401
    && options.authenticated !== false
    && options.retryUnauthorized !== false
    && authentication
  ) {
    const currentAccessToken = authentication.getAccessToken()
    let refreshedAccessToken: string | null
    if (currentAccessToken === requestAccessToken) {
      refreshedAccessToken = await refreshAccessToken(requestAccessToken)
    } else if (refreshPromise && refreshFromToken === requestAccessToken) {
      refreshedAccessToken = await refreshPromise
    } else {
      refreshedAccessToken = refreshTransition?.from === requestAccessToken
        && refreshTransition.to === currentAccessToken ? currentAccessToken : null
    }
    if (refreshedAccessToken) {
      headers.set('Authorization', `Bearer ${refreshedAccessToken}`)
      try { response = await fetch(path, { ...init, headers }) }
      catch { throw new ApiError(0, 'NETWORK_ERROR', '服务暂不可用，请稍后重试。') }
    }
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as ApiErrorEnvelope
    throw new ApiError(
      response.status, payload.error?.code ?? 'HTTP_ERROR',
      payload.error?.message ?? (response.status >= 500
        ? '服务暂不可用，请稍后重试。'
        : '请求失败，请稍后重试。'),
      payload.error?.request_id ?? response.headers.get('X-Request-ID') ?? undefined,
    )
  }
  if (response.status === 204) return undefined as T
  return await response.json() as T
}

export function formatApiError(error: unknown) {
  if (!(error instanceof ApiError)) return error instanceof Error ? error.message : '请求失败。'
  const code = error.code ? ` [${error.code}]` : ''
  const requestId = error.requestId ? ` 请求标识：${error.requestId}` : ''
  return `${error.message}${code}${requestId}`
}

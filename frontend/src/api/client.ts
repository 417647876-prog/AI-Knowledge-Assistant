import type { ApiErrorEnvelope } from '../types/api'

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

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try { response = await fetch(path, init) }
  catch { throw new ApiError(0, 'NETWORK_ERROR', '服务暂不可用，请稍后重试。') }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as ApiErrorEnvelope
    throw new ApiError(
      response.status, payload.error?.code ?? 'HTTP_ERROR',
      payload.error?.message ?? '请求失败，请稍后重试。', payload.error?.request_id,
    )
  }
  return await response.json() as T
}

export function formatApiError(error: unknown) {
  if (!(error instanceof ApiError)) return error instanceof Error ? error.message : '请求失败。'
  const code = error.code ? ` [${error.code}]` : ''
  const requestId = error.requestId ? ` 请求标识：${error.requestId}` : ''
  return `${error.message}${code}${requestId}`
}

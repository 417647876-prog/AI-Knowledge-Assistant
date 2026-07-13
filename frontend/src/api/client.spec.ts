import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiRequest, formatApiError } from './client'

afterEach(() => vi.unstubAllGlobals())

describe('apiRequest', () => {
  it('returns JSON on success', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"status":"ready"}', {
      status: 200, headers: { 'Content-Type': 'application/json' },
    })))
    await expect(apiRequest('/ready')).resolves.toEqual({ status: 'ready' })
  })

  it('maps FastAPI errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: { code: 'FILE_TOO_LARGE', message: '文件超过 20 MB 限制。', request_id: 'req-1' },
    }), { status: 413, headers: { 'Content-Type': 'application/json' } })))
    await expect(apiRequest('/upload')).rejects.toEqual(
      new ApiError(413, 'FILE_TOO_LARGE', '文件超过 20 MB 限制。', 'req-1'),
    )
  })

  it('maps network errors safely', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))
    await expect(apiRequest('/ready')).rejects.toMatchObject({
      code: 'NETWORK_ERROR', message: '服务暂不可用，请稍后重试。',
    })
  })

  it('formats code and request id for the UI', () => {
    const error = new ApiError(413, 'FILE_TOO_LARGE', '文件过大。', 'req-1')
    expect(formatApiError(error)).toBe('文件过大。 [FILE_TOO_LARGE] 请求标识：req-1')
  })
})

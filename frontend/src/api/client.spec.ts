import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiRequest, authenticatedFetch, configureAuthentication, formatApiError } from './client'

afterEach(() => {
  vi.unstubAllGlobals()
  configureAuthentication({
    getAccessToken: () => null,
    refreshAccessToken: async () => null,
    onAuthenticationFailed: () => undefined,
  })
})

describe('apiRequest', () => {
  it('returns the successful raw response after one initial 401 refresh', async () => {
    let token = 'old'
    configureAuthentication({
      getAccessToken: () => token,
      refreshAccessToken: vi.fn(async () => { token = 'new'; return token }),
      onAuthenticationFailed: vi.fn(),
    })
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response('{}', { status: 401 }))
      .mockResolvedValueOnce(new Response('stream', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    const response = await authenticatedFetch('/stream', { method: 'POST' })

    expect(await response.text()).toBe('stream')
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('keeps AbortError so the conversation store can mark stopped', async () => {
    const aborted = new DOMException('aborted', 'AbortError')
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(aborted))

    await expect(authenticatedFetch('/stream')).rejects.toBe(aborted)
  })

  it('returns JSON on success', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"status":"ready"}', {
      status: 200, headers: { 'Content-Type': 'application/json' },
    })))
    await expect(apiRequest('/ready')).resolves.toEqual({ status: 'ready' })
  })

  it('adds the in-memory access token to authenticated requests', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"status":"ready"}', {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)
    configureAuthentication({
      getAccessToken: () => 'access-token',
      refreshAccessToken: vi.fn(),
      onAuthenticationFailed: vi.fn(),
    })

    await apiRequest('/ready')

    expect(fetchMock).toHaveBeenCalledWith('/ready', expect.objectContaining({
      headers: expect.any(Headers),
    }))
    const headers = fetchMock.mock.calls[0]![1]!.headers as Headers
    expect(headers.get('Authorization')).toBe('Bearer access-token')
  })

  it('shares one refresh when concurrent requests receive 401 responses', async () => {
    let accessToken = 'old-token'
    const refresh = vi.fn().mockImplementation(async () => {
      accessToken = 'new-token'
      return accessToken
    })
    configureAuthentication({
      getAccessToken: () => accessToken,
      refreshAccessToken: refresh,
      onAuthenticationFailed: vi.fn(),
    })
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response('{}', { status: 401 }))
      .mockResolvedValueOnce(new Response('{}', { status: 401 }))
      .mockImplementation(() => Promise.resolve(new Response('{"ok":true}', {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })))
    vi.stubGlobal('fetch', fetchMock)

    await expect(Promise.all([apiRequest('/one'), apiRequest('/two')])).resolves.toEqual([
      { ok: true }, { ok: true },
    ])

    expect(refresh).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledTimes(4)
    for (const call of fetchMock.mock.calls.slice(2)) {
      expect((call[1]!.headers as Headers).get('Authorization')).toBe('Bearer new-token')
    }
  })

  it('reuses the refreshed token when a concurrent old-token 401 arrives late', async () => {
    let accessToken = 'old-token'
    let resolveLateResponse!: (response: Response) => void
    const refreshAccessToken = vi.fn().mockImplementation(async () => {
      accessToken = 'new-token'
      return accessToken
    })
    configureAuthentication({
      getAccessToken: () => accessToken,
      refreshAccessToken,
      onAuthenticationFailed: vi.fn(),
    })
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response('{}', { status: 401 }))
      .mockReturnValueOnce(new Promise<Response>((resolve) => { resolveLateResponse = resolve }))
      .mockImplementation(() => Promise.resolve(new Response('{"ok":true}', {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })))
    vi.stubGlobal('fetch', fetchMock)

    const first = apiRequest('/one')
    const second = apiRequest('/two')
    await expect(first).resolves.toEqual({ ok: true })
    resolveLateResponse(new Response('{}', { status: 401 }))
    await expect(second).resolves.toEqual({ ok: true })

    expect(refreshAccessToken).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledTimes(4)
  })

  it('does not replay an old-account request with a newly logged-in user token', async () => {
    let accessToken = 'old-user-token'
    let resolveResponse!: (response: Response) => void
    const refreshAccessToken = vi.fn()
    configureAuthentication({
      getAccessToken: () => accessToken,
      refreshAccessToken,
      onAuthenticationFailed: vi.fn(),
    })
    const fetchMock = vi.fn().mockReturnValue(new Promise<Response>((resolve) => {
      resolveResponse = resolve
    }))
    vi.stubGlobal('fetch', fetchMock)

    const oldRequest = apiRequest('/transfer', { method: 'POST' })
    accessToken = 'new-user-token'
    resolveResponse(new Response('{}', { status: 401 }))

    await expect(oldRequest).rejects.toMatchObject({ status: 401 })
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(refreshAccessToken).not.toHaveBeenCalled()
  })

  it('notifies authentication failure once when a shared refresh fails', async () => {
    const onAuthenticationFailed = vi.fn()
    const refreshAccessToken = vi.fn().mockResolvedValue(null)
    configureAuthentication({
      getAccessToken: () => 'expired-token',
      refreshAccessToken,
      onAuthenticationFailed,
    })
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(
      new Response('{}', { status: 401 }),
    )))

    const results = await Promise.allSettled([apiRequest('/one'), apiRequest('/two')])

    expect(results.every((result) => result.status === 'rejected')).toBe(true)
    expect(refreshAccessToken).toHaveBeenCalledOnce()
    expect(onAuthenticationFailed).toHaveBeenCalledOnce()
  })

  it('does not repeat a failed refresh for a late old-token 401', async () => {
    let accessToken: string | null = 'old-token'
    let resolveLateResponse!: (response: Response) => void
    const onAuthenticationFailed = vi.fn(() => { accessToken = null })
    const refreshAccessToken = vi.fn().mockResolvedValue(null)
    configureAuthentication({
      getAccessToken: () => accessToken,
      refreshAccessToken,
      onAuthenticationFailed,
    })
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response('{}', { status: 401 }))
      .mockReturnValueOnce(new Promise<Response>((resolve) => { resolveLateResponse = resolve }))
    vi.stubGlobal('fetch', fetchMock)

    const first = apiRequest('/one')
    const second = apiRequest('/two')
    await expect(first).rejects.toMatchObject({ status: 401 })
    resolveLateResponse(new Response('{}', { status: 401 }))
    await expect(second).rejects.toMatchObject({ status: 401 })

    expect(refreshAccessToken).toHaveBeenCalledOnce()
    expect(onAuthenticationFailed).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('replays a request at most once when it remains unauthorized', async () => {
    const refreshAccessToken = vi.fn().mockResolvedValue('new-token')
    configureAuthentication({
      getAccessToken: () => 'old-token',
      refreshAccessToken,
      onAuthenticationFailed: vi.fn(),
    })
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(
      new Response('{}', { status: 401 }),
    ))
    vi.stubGlobal('fetch', fetchMock)

    await expect(apiRequest('/protected')).rejects.toMatchObject({ status: 401 })

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(refreshAccessToken).toHaveBeenCalledOnce()
  })

  it('maps FastAPI errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: { code: 'FILE_TOO_LARGE', message: '文件超过 20 MB 限制。', request_id: 'req-1' },
    }), { status: 413, headers: { 'Content-Type': 'application/json' } })))
    await expect(apiRequest('/upload')).rejects.toEqual(
      new ApiError(413, 'FILE_TOO_LARGE', '文件超过 20 MB 限制。', 'req-1'),
    )
  })

  it('maps standard 422 errors and falls back to the response request id header', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: [{ loc: ['body', 'name'], msg: 'Field required', type: 'missing' }],
    }), {
      status: 422,
      headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'req-header-422' },
    })))

    await expect(apiRequest('/knowledge-bases')).rejects.toEqual(
      new ApiError(422, 'HTTP_ERROR', '请求失败，请稍后重试。', 'req-header-422'),
    )
  })

  it('uses a service unavailable message for ordinary server errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('Internal Server Error', {
      status: 500,
      headers: { 'Content-Type': 'text/plain', 'X-Request-ID': 'req-header-500' },
    })))

    await expect(apiRequest('/ready')).rejects.toEqual(
      new ApiError(500, 'HTTP_ERROR', '服务暂不可用，请稍后重试。', 'req-header-500'),
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

import { afterEach, describe, expect, it, vi } from 'vitest'
import { pollDocumentStatus } from './documents'
import type { DocumentTask } from '../types/api'

const task = (status: DocumentTask['status']): DocumentTask => ({
  document_id: 'doc-1', job_id: 'job-1', status, error_code: null, error_message: null,
})

afterEach(() => vi.useRealTimers())

describe('pollDocumentStatus', () => {
  it('stops when ready', async () => {
    const request = vi.fn().mockResolvedValueOnce(task('running')).mockResolvedValueOnce(task('ready'))
    const result = await pollDocumentStatus('doc-1', {
      request, sleep: () => Promise.resolve(), intervalMs: 0, timeoutMs: 100,
    })
    expect(result.status).toBe('ready')
    expect(request).toHaveBeenCalledTimes(2)
  })

  it('returns failed state', async () => {
    const request = vi.fn().mockResolvedValue(task('failed'))
    await expect(pollDocumentStatus('doc-1', {
      request, sleep: () => Promise.resolve(), intervalMs: 0, timeoutMs: 100,
    })).resolves.toMatchObject({ status: 'failed' })
  })

  it('throws after timeout', async () => {
    const request = vi.fn().mockResolvedValue(task('running'))
    let now = 0
    await expect(pollDocumentStatus('doc-1', {
      request, sleep: async () => { now = 101 }, now: () => now,
      intervalMs: 0, timeoutMs: 100,
    })).rejects.toMatchObject({ code: 'DOCUMENT_POLL_TIMEOUT' })
  })

  it('does not start another request at the deadline', async () => {
    const request = vi.fn().mockResolvedValue(task('running'))
    let now = 0
    await expect(pollDocumentStatus('doc-1', {
      request, sleep: async () => { now = 100 }, now: () => now,
      intervalMs: 0, timeoutMs: 100,
    })).rejects.toMatchObject({ code: 'DOCUMENT_POLL_TIMEOUT' })
    expect(request).toHaveBeenCalledTimes(1)
  })

  it('cancels an unfinished request when the remaining time expires', async () => {
    vi.useFakeTimers()
    let requestSignal: AbortSignal | undefined
    const request = vi.fn((_id: string, signal?: AbortSignal) => {
      requestSignal = signal
      if (!signal) return Promise.reject(new Error('轮询请求缺少取消信号'))
      return new Promise<DocumentTask>((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
      })
    })

    const result = pollDocumentStatus('doc-1', { request, timeoutMs: 100 })
    const rejection = expect(result).rejects.toMatchObject({ code: 'DOCUMENT_POLL_TIMEOUT' })
    await vi.advanceTimersByTimeAsync(100)

    await rejection
    expect(requestSignal?.aborted).toBe(true)
  })
})

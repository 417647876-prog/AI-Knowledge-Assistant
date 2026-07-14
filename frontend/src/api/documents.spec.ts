import { afterEach, describe, expect, it, vi } from 'vitest'
import { deleteDocument, listDocuments, pollDocumentStatus, reprocessDocument } from './documents'
import type { DocumentTask } from '../types/api'

const task = (status: DocumentTask['status']): DocumentTask => ({
  document_id: 'doc-1', job_id: 'job-1', file_name: '员工手册.txt', status, error_code: null, error_message: null,
})

afterEach(() => vi.useRealTimers())

describe('pollDocumentStatus', () => {
  it('stops when ready', async () => {
    const request = vi.fn().mockResolvedValueOnce(task('parsing')).mockResolvedValueOnce(task('ready'))
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
    const request = vi.fn().mockResolvedValue(task('embedding'))
    let now = 0
    await expect(pollDocumentStatus('doc-1', {
      request, sleep: async () => { now = 101 }, now: () => now,
      intervalMs: 0, timeoutMs: 100,
    })).rejects.toMatchObject({ code: 'DOCUMENT_POLL_TIMEOUT' })
  })

  it('does not start another request at the deadline', async () => {
    const request = vi.fn().mockResolvedValue(task('embedding'))
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

describe('文档管理 API', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('请求列表、重处理和删除端点', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [task('ready')] }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify(task('pending')), {
        status: 202, headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(listDocuments('kb-1')).resolves.toEqual([task('ready')])
    await expect(reprocessDocument('doc-1')).resolves.toEqual(task('pending'))
    await expect(deleteDocument('doc-1')).resolves.toBeUndefined()

    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ['/api/v1/knowledge-bases/kb-1/documents', undefined],
      ['/api/v1/documents/doc-1/reprocess', 'POST'],
      ['/api/v1/documents/doc-1', 'DELETE'],
    ])
  })
})

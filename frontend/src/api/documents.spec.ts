import { describe, expect, it, vi } from 'vitest'
import { pollDocumentStatus } from './documents'
import type { DocumentTask } from '../types/api'

const task = (status: DocumentTask['status']): DocumentTask => ({
  document_id: 'doc-1', job_id: 'job-1', status, error_code: null, error_message: null,
})

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
})

import type { DocumentListResponse, DocumentTask } from '../types/api'
import { ApiError, apiRequest } from './client'

export function uploadDocument(knowledgeBaseId: string, file: File) {
  const body = new FormData()
  body.append('file', file)
  return apiRequest<DocumentTask>(`/api/v1/knowledge-bases/${knowledgeBaseId}/documents`, {
    method: 'POST', body,
  })
}

export const getDocument = (id: string, signal?: AbortSignal) =>
  apiRequest<DocumentTask>(`/api/v1/documents/${id}`, { signal })

export async function listDocuments(knowledgeBaseId: string): Promise<DocumentTask[]> {
  const response = await apiRequest<DocumentListResponse>(
    `/api/v1/knowledge-bases/${knowledgeBaseId}/documents`,
  )
  return response.items
}

export const reprocessDocument = (id: string) =>
  apiRequest<DocumentTask>(`/api/v1/documents/${id}/reprocess`, { method: 'POST' })

export const deleteDocument = (id: string) =>
  apiRequest<void>(`/api/v1/documents/${id}`, { method: 'DELETE' })

interface PollOptions {
  intervalMs?: number
  timeoutMs?: number
  request?: typeof getDocument
  sleep?: (milliseconds: number) => Promise<void>
  now?: () => number
}

export async function pollDocumentStatus(id: string, options: PollOptions = {}) {
  const intervalMs = options.intervalMs ?? 2_000
  const timeoutMs = options.timeoutMs ?? 120_000
  const request = options.request ?? getDocument
  const sleep = options.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)))
  const now = options.now ?? Date.now
  const deadline = now() + timeoutMs
  const timeoutError = () => new ApiError(
    0, 'DOCUMENT_POLL_TIMEOUT', '文档处理时间较长，请稍后刷新状态。',
  )

  while (true) {
    const remainingMs = deadline - now()
    if (remainingMs <= 0) throw timeoutError()

    const controller = new AbortController()
    let timeoutId: ReturnType<typeof setTimeout> | undefined
    const timeout = new Promise<never>((_resolve, reject) => {
      timeoutId = setTimeout(() => {
        controller.abort()
        reject(timeoutError())
      }, remainingMs)
    })

    let document: DocumentTask
    try {
      document = await Promise.race([request(id, controller.signal), timeout])
    } catch (error) {
      if (controller.signal.aborted) throw timeoutError()
      throw error
    } finally {
      clearTimeout(timeoutId)
    }

    if (now() >= deadline) throw timeoutError()
    if (document.status === 'ready' || document.status === 'failed') return document
    await sleep(Math.min(intervalMs, deadline - now()))
  }
}

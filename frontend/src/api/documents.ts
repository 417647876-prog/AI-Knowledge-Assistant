import type { DocumentTask } from '../types/api'
import { ApiError, apiRequest } from './client'

export function uploadDocument(knowledgeBaseId: string, file: File) {
  const body = new FormData()
  body.append('file', file)
  return apiRequest<DocumentTask>(`/api/v1/knowledge-bases/${knowledgeBaseId}/documents`, {
    method: 'POST', body,
  })
}

export const getDocument = (id: string) =>
  apiRequest<DocumentTask>(`/api/v1/documents/${id}`)

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

  while (true) {
    const document = await request(id)
    if (document.status === 'ready' || document.status === 'failed') return document
    if (now() >= deadline)
      throw new ApiError(0, 'DOCUMENT_POLL_TIMEOUT', '文档处理时间较长，请稍后刷新状态。')
    await sleep(intervalMs)
  }
}

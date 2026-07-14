import type { QuestionResponse } from '../types/api'
import type { ConversationHistory, QuestionStreamEvent } from '../types/conversation'
import { ApiError, apiRequest, authenticatedFetch } from './client'
import { parseSse } from './sse'

export const askQuestion = (knowledgeBaseId: string, question: string, topK = 5) =>
  apiRequest<QuestionResponse>(`/api/v1/knowledge-bases/${knowledgeBaseId}/questions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, top_k: topK }),
  })

const questionEventNames = new Set<QuestionStreamEvent['event']>([
  'status', 'rewrite', 'retrieval', 'token', 'citation', 'done', 'error',
])

export async function* streamQuestion(
  knowledgeBaseId: string,
  question: string,
  history: ConversationHistory[],
  signal: AbortSignal,
  topK = 5,
): AsyncGenerator<QuestionStreamEvent> {
  const response = await authenticatedFetch(
    `/api/v1/knowledge-bases/${knowledgeBaseId}/questions/stream`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ question, top_k: topK, history }),
      signal,
    },
  )
  if (!response.body) {
    throw new ApiError(0, 'STREAM_UNAVAILABLE', '浏览器无法读取流式回答。')
  }

  for await (const raw of parseSse(response.body)) {
    if (!questionEventNames.has(raw.event as QuestionStreamEvent['event'])) {
      console.debug('Ignored unknown question stream event', raw.event)
      continue
    }

    let data: unknown
    try {
      data = JSON.parse(raw.data)
    } catch {
      throw new ApiError(0, 'INVALID_STREAM', '流式响应格式错误。')
    }

    const event = { event: raw.event, data } as QuestionStreamEvent
    yield event
    if (event.event === 'done' || event.event === 'error') return
  }

  throw new ApiError(0, 'STREAM_INTERRUPTED', '回答连接意外中断。')
}

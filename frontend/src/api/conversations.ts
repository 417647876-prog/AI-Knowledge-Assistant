import type {
  ConversationDetail,
  ConversationPage,
  ConversationSummary,
  PageOptions,
} from '../types/api'
import type { QuestionStreamEvent } from '../types/conversation'
import { ApiError, apiRequest, authenticatedFetch } from './client'
import { parseSse } from './sse'

export type StreamConversationMessageInput =
  | { question: string; retry_of_message_id?: never; top_k?: number }
  | { question?: never; retry_of_message_id: string; top_k?: number }

const conversationEventNames = new Set<QuestionStreamEvent['event']>([
  'status', 'rewrite', 'retrieval', 'token', 'citation', 'done', 'error',
])

const jsonHeaders = { 'Content-Type': 'application/json' }

export function createConversation(
  knowledgeBaseId: string,
  title = '新会话',
): Promise<ConversationSummary> {
  return apiRequest<ConversationSummary>(
    `/api/v1/knowledge-bases/${knowledgeBaseId}/conversations`,
    { method: 'POST', headers: jsonHeaders, body: JSON.stringify({ title }) },
  )
}

export function listConversations(
  knowledgeBaseId: string,
  options: PageOptions = {},
): Promise<ConversationPage> {
  const query = new URLSearchParams()
  query.set('page', String(options.page ?? 1))
  query.set('page_size', String(options.pageSize ?? 20))
  return apiRequest<ConversationPage>(
    `/api/v1/knowledge-bases/${knowledgeBaseId}/conversations?${query}`,
  )
}

export const getConversation = (conversationId: string) =>
  apiRequest<ConversationDetail>(`/api/v1/conversations/${conversationId}`)

export const deleteConversation = (conversationId: string) =>
  apiRequest<void>(`/api/v1/conversations/${conversationId}`, { method: 'DELETE' })

export async function* streamConversationMessage(
  conversationId: string,
  input: StreamConversationMessageInput,
  signal: AbortSignal,
): AsyncGenerator<QuestionStreamEvent> {
  const response = await authenticatedFetch(
    `/api/v1/conversations/${conversationId}/messages/stream`,
    {
      method: 'POST',
      headers: { ...jsonHeaders, Accept: 'text/event-stream' },
      body: JSON.stringify(input),
      signal,
    },
  )
  if (!response.body) {
    throw new ApiError(0, 'STREAM_UNAVAILABLE', '浏览器无法读取流式回答。')
  }

  for await (const raw of parseSse(response.body)) {
    if (!conversationEventNames.has(raw.event as QuestionStreamEvent['event'])) {
      console.debug('Ignored unknown conversation stream event', raw.event)
      continue
    }
    let data: unknown
    try { data = JSON.parse(raw.data) }
    catch { throw new ApiError(0, 'INVALID_STREAM', '流式响应格式错误。') }

    const event = { event: raw.event, data } as QuestionStreamEvent
    yield event
    if (event.event === 'done' || event.event === 'error') return
  }

  throw new ApiError(0, 'STREAM_INTERRUPTED', '回答连接意外中断。')
}

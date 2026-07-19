import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
  streamConversationMessage,
} from './conversations'

const summary = {
  id: 'conversation-1',
  knowledge_base_id: 'kb-1',
  title: '制度问答',
  created_at: '2026-07-19T08:00:00Z',
  updated_at: '2026-07-19T08:00:00Z',
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function streamResponse(content: string): Response {
  const bytes = new TextEncoder().encode(content)
  return new Response(new ReadableStream<Uint8Array>({
    start(controller) { controller.enqueue(bytes); controller.close() },
  }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

afterEach(() => vi.unstubAllGlobals())

describe('服务端会话 API', () => {
  it('使用后端现有的创建、分页、详情和删除端点', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(summary, 201))
      .mockResolvedValueOnce(jsonResponse({ items: [summary], page: 2, page_size: 10, total: 11 }))
      .mockResolvedValueOnce(jsonResponse({ ...summary, messages: [] }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    await createConversation('kb-1', '制度问答')
    await listConversations('kb-1', { page: 2, pageSize: 10 })
    await getConversation('conversation-1')
    await deleteConversation('conversation-1')

    expect(fetchMock.mock.calls.map(([path, init]) => [path, init?.method])).toEqual([
      ['/api/v1/knowledge-bases/kb-1/conversations', 'POST'],
      ['/api/v1/knowledge-bases/kb-1/conversations?page=2&page_size=10', undefined],
      ['/api/v1/conversations/conversation-1', undefined],
      ['/api/v1/conversations/conversation-1', 'DELETE'],
    ])
    expect(JSON.parse(fetchMock.mock.calls[0]![1].body as string)).toEqual({ title: '制度问答' })
  })

  it('发送显式重试请求并解析会话 SSE 事件', async () => {
    const fetchMock = vi.fn().mockResolvedValue(streamResponse(
      'event: token\ndata: {"delta":"新回答"}\n\n'
      + 'event: done\ndata: {"request_id":"req-1","citations":[],"retrieved_chunk_count":0,"timings":{"rewrite_ms":0,"retrieval_ms":0,"generation_ms":1,"total_ms":1}}\n\n',
    ))
    vi.stubGlobal('fetch', fetchMock)

    const events = []
    for await (const event of streamConversationMessage(
      'conversation-1',
      { retry_of_message_id: 'message-1', top_k: 6 },
      new AbortController().signal,
    )) events.push(event)

    expect(events.map((event) => event.event)).toEqual(['token', 'done'])
    expect(fetchMock.mock.calls[0]![0]).toBe(
      '/api/v1/conversations/conversation-1/messages/stream',
    )
    expect(JSON.parse(fetchMock.mock.calls[0]![1].body as string)).toEqual({
      retry_of_message_id: 'message-1', top_k: 6,
    })
  })
})

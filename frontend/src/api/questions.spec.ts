import { afterEach, describe, expect, it, vi } from 'vitest'
import { streamQuestion } from './questions'

function responseOf(content: string): Response {
  const bytes = new TextEncoder().encode(content)
  return new Response(new ReadableStream<Uint8Array>({
    start(controller) { controller.enqueue(bytes); controller.close() },
  }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

async function read(content: string) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(responseOf(content)))
  const events = []
  for await (const event of streamQuestion('kb-1', '问题', [], new AbortController().signal)) {
    events.push(event)
  }
  return events
}

afterEach(() => vi.unstubAllGlobals())

describe('streamQuestion', () => {
  it('logs unknown events and continues yielding known events', async () => {
    const debug = vi.spyOn(console, 'debug').mockImplementation(() => undefined)
    const events = await read(
      'event: progress\ndata: {}\n\nevent: token\ndata: {"delta":"回答"}\n\nevent: done\ndata: {"request_id":"r","citations":[],"retrieved_chunk_count":0,"timings":{"rewrite_ms":0,"retrieval_ms":0,"generation_ms":0,"total_ms":0}}\n\n',
    )

    expect(events.map((item) => item.event)).toEqual(['token', 'done'])
    expect(debug).toHaveBeenCalledWith('Ignored unknown question stream event', 'progress')
  })

  it('rejects invalid JSON payloads', async () => {
    await expect(read('event: token\ndata: not-json\n\n'))
      .rejects.toMatchObject({ code: 'INVALID_STREAM' })
  })

  it('rejects a completed transport without a terminal event', async () => {
    await expect(read('event: token\ndata: {"delta":"半段"}\n\n'))
      .rejects.toMatchObject({ code: 'STREAM_INTERRUPTED' })
  })

  it('treats an error event as terminal', async () => {
    const events = await read(
      'event: error\ndata: {"code":"X","message":"失败","request_id":"r"}\n\n',
    )

    expect(events[0]?.event).toBe('error')
  })

  it('stops after done and does not yield later tokens', async () => {
    const events = await read(
      'event: done\ndata: {"request_id":"r","citations":[],"retrieved_chunk_count":0,"timings":{"rewrite_ms":0,"retrieval_ms":0,"generation_ms":0,"total_ms":0}}\n\nevent: token\ndata: {"delta":"不应输出"}\n\n',
    )

    expect(events.map((item) => item.event)).toEqual(['done'])
  })

  it('stops after error and does not yield later terminal or token events', async () => {
    const events = await read(
      'event: error\ndata: {"code":"X","message":"失败","request_id":"r"}\n\nevent: done\ndata: {"request_id":"r","citations":[],"retrieved_chunk_count":0,"timings":{"rewrite_ms":0,"retrieval_ms":0,"generation_ms":0,"total_ms":0}}\n\nevent: token\ndata: {"delta":"不应输出"}\n\n',
    )

    expect(events.map((item) => item.event)).toEqual(['error'])
  })
})

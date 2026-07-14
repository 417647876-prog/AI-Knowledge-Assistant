import { describe, expect, it } from 'vitest'
import { parseSse } from './sse'

const streamOf = (...chunks: Uint8Array[]) => new ReadableStream<Uint8Array>({
  start(controller) { chunks.forEach((chunk) => controller.enqueue(chunk)); controller.close() },
})

async function readAll(stream: ReadableStream<Uint8Array>) {
  const events = []
  for await (const event of parseSse(stream)) events.push(event)
  return events
}

describe('parseSse', () => {
  it('parses utf8 characters and events split across network chunks', async () => {
    const bytes = new TextEncoder().encode(
      ': ping\n\nevent: token\ndata: {"delta":"中文"}\n\nevent: done\ndata: {}\n\n',
    )

    const events = await readAll(streamOf(
      bytes.slice(0, 31), bytes.slice(31, 39), bytes.slice(39),
    ))

    expect(events).toEqual([
      { event: 'token', data: '{"delta":"中文"}' },
      { event: 'done', data: '{}' },
    ])
  })

  it('rejects an incomplete final event', async () => {
    const incomplete = new TextEncoder().encode('event: token\ndata: {}')

    await expect(readAll(streamOf(incomplete)))
      .rejects.toMatchObject({ code: 'STREAM_INTERRUPTED' })
  })

  it('flushes a complete final event', async () => {
    const complete = new TextEncoder().encode('event: token\ndata: {}\n\n')

    await expect(readAll(streamOf(complete))).resolves.toEqual([
      { event: 'token', data: '{}' },
    ])
  })
})

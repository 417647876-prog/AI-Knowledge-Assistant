import { ApiError } from './client'

export interface RawSseEvent {
  event: string
  data: string
}

function parseBlock(block: string): RawSseEvent | null {
  let event = 'message'
  const data: string[] = []

  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith(':')) continue
    if (line.startsWith('event:')) event = line.slice(6).trim()
    if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
  }

  return data.length ? { event, data: data.join('\n') } : null
}

export async function* parseSse(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<RawSseEvent> {
  const reader = stream.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      buffer = buffer.replace(/\r\n/g, '\n')
      let boundary = buffer.indexOf('\n\n')
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const event = parseBlock(block)
        if (event) yield event
        boundary = buffer.indexOf('\n\n')
      }
    }

    buffer += decoder.decode()
    if (buffer.trim()) {
      throw new ApiError(0, 'STREAM_INTERRUPTED', '回答连接意外中断。')
    }
  } finally {
    reader.releaseLock()
  }
}

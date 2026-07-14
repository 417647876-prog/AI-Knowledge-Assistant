import { createPinia, setActivePinia } from 'pinia'
import { watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/questions', () => ({ streamQuestion: vi.fn() }))

import { streamQuestion } from '../api/questions'
import { conversationStorageKey } from './conversationStorage'
import { useConversationsStore } from './conversations'
import type { Citation } from '../types/api'
import type { QuestionStreamEvent } from '../types/conversation'

const citation = (citationId: number): Citation => ({
  citation_id: citationId, document_id: `doc-${citationId}`, file_name: '员工手册.txt',
  content: '带薪年假说明', relevance_score: 0.9, page_number: 1, sheet_name: null,
  row_start: null, section_title: '年假',
})

async function* events(...items: QuestionStreamEvent[]) {
  for (const item of items) yield item
}

describe('conversations store', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    setActivePinia(createPinia())
    sessionStorage.clear()
  })

  afterEach(() => vi.useRealTimers())

  it('累积流式事件并保存已完成回答', async () => {
    vi.mocked(streamQuestion).mockReturnValue(events(
      { event: 'rewrite', data: { standalone_question: '独立问题', elapsed_ms: 12 } },
      { event: 'retrieval', data: { retrieved_chunk_count: 1, elapsed_ms: 8 } },
      { event: 'token', data: { delta: '答案。[1]' } },
      { event: 'citation', data: citation(1) },
      {
        event: 'done', data: {
          request_id: 'req-1', citations: [citation(1)], retrieved_chunk_count: 1,
          timings: { rewrite_ms: 12, retrieval_ms: 8, generation_ms: 30, total_ms: 50 },
        },
      },
    ))
    const store = useConversationsStore()
    store.activate('u-1', 'kb-1')

    await store.submit('它呢？')

    expect(store.messages[store.messages.length - 1]).toMatchObject({
      kind: 'assistant', status: 'completed', content: '答案。[1]', requestId: 'req-1',
      standaloneQuestion: '独立问题', retrievedChunkCount: 1, citations: [citation(1)],
    })
    expect(streamQuestion).toHaveBeenCalledWith(
      'kb-1', '它呢？', [], expect.any(AbortSignal),
    )
    expect(sessionStorage.getItem(conversationStorageKey('u-1', 'kb-1'))).toContain('答案。[1]')
  })

  it('流完成时触发回答状态的响应式更新', async () => {
    vi.mocked(streamQuestion).mockReturnValue(events({
      event: 'done', data: {
        request_id: 'req-reactive', citations: [], retrieved_chunk_count: 0,
        timings: { rewrite_ms: 0, retrieval_ms: 0, generation_ms: 1, total_ms: 1 },
      },
    }))
    const store = useConversationsStore()
    const statuses: (string | null)[] = []
    const stopWatching = watch(
      () => {
        const last = store.messages[store.messages.length - 1]
        return last?.kind === 'assistant' ? last.status : null
      },
      (status) => statuses.push(status),
      { flush: 'sync' },
    )
    store.activate('u-1', 'kb-1')

    await store.submit('问题')
    stopWatching()

    expect(statuses).toContain('completed')
  })

  it('将主动中止的部分回答标记为已停止且不进入下一轮上下文', async () => {
    vi.mocked(streamQuestion)
      .mockImplementationOnce(async function* (_kb, _q, _history, signal) {
        yield { event: 'token', data: { delta: '半段' } }
        await new Promise<void>((_resolve, reject) => signal.addEventListener('abort', () =>
          reject(new DOMException('aborted', 'AbortError'))))
      })
      .mockReturnValueOnce(events({
        event: 'done', data: {
          request_id: 'req-next', citations: [], retrieved_chunk_count: 0,
          timings: { rewrite_ms: 0, retrieval_ms: 0, generation_ms: 1, total_ms: 1 },
        },
      }))
    const store = useConversationsStore()
    store.activate('u-1', 'kb-1')
    const pending = store.submit('问题')
    await vi.waitFor(() => expect(store.messages[store.messages.length - 1]).toMatchObject({ content: '半段' }))

    store.stop()
    await pending
    await store.submit('下一题')

    expect(store.messages[store.messages.length - 3]).toMatchObject({ status: 'stopped', content: '半段' })
    expect(streamQuestion).toHaveBeenLastCalledWith('kb-1', '下一题', [], expect.any(AbortSignal))
  })

  it('保留失败回答的部分文本、错误码和请求标识', async () => {
    vi.mocked(streamQuestion).mockReturnValue(events(
      { event: 'token', data: { delta: '半段' } },
      { event: 'error', data: { code: 'CHAT_PROVIDER_ERROR', message: '模型不可用', request_id: 'req-fail' } },
    ))
    const store = useConversationsStore()
    store.activate('u-1', 'kb-1')

    await store.submit('问题')

    expect(store.messages[store.messages.length - 1]).toMatchObject({
      status: 'failed', content: '半段', errorCode: 'CHAT_PROVIDER_ERROR', requestId: 'req-fail',
    })
  })

  it('切换知识库时中止旧流并使用新知识库发问', async () => {
    let oldSignal: AbortSignal | undefined
    vi.mocked(streamQuestion)
      .mockImplementationOnce(async function* (_kb, _q, _history, signal) {
        oldSignal = signal
        yield { event: 'token', data: { delta: '旧回答' } }
        await new Promise<void>(() => {})
      })
      .mockReturnValueOnce(events({
        event: 'done', data: {
          request_id: 'req-2', citations: [], retrieved_chunk_count: 0,
          timings: { rewrite_ms: 0, retrieval_ms: 0, generation_ms: 1, total_ms: 1 },
        },
      }))
    const store = useConversationsStore()
    store.activate('u-1', 'kb-1')
    void store.submit('问题')
    await vi.waitFor(() => expect(oldSignal).toBeDefined())

    store.activate('u-1', 'kb-2')
    await store.submit('问题')

    expect(oldSignal?.aborted).toBe(true)
    expect(streamQuestion).toHaveBeenLastCalledWith('kb-2', '问题', [], expect.any(AbortSignal))
  })

  it('在流式增量后等待二百毫秒再保存新快照', async () => {
    vi.useFakeTimers()
    let release!: () => void
    vi.mocked(streamQuestion).mockImplementation(async function* () {
      yield { event: 'token', data: { delta: '延迟保存' } }
      await new Promise<void>((resolve) => { release = resolve })
      yield {
        event: 'done', data: {
          request_id: 'req-delay', citations: [], retrieved_chunk_count: 0,
          timings: { rewrite_ms: 0, retrieval_ms: 0, generation_ms: 1, total_ms: 1 },
        },
      }
    })
    const store = useConversationsStore()
    store.activate('u-1', 'kb-1')
    const pending = store.submit('问题')
    await Promise.resolve()
    await Promise.resolve()
    expect(store.messages[store.messages.length - 1]).toMatchObject({ content: '延迟保存' })

    const key = conversationStorageKey('u-1', 'kb-1')
    expect(sessionStorage.getItem(key)).not.toContain('延迟保存')
    await vi.advanceTimersByTimeAsync(199)
    expect(sessionStorage.getItem(key)).not.toContain('延迟保存')
    await vi.advanceTimersByTimeAsync(1)
    expect(sessionStorage.getItem(key)).toContain('延迟保存')

    release()
    await pending
  })

  it('重试时替换失败回答而不重复追加用户问题', async () => {
    vi.mocked(streamQuestion)
      .mockReturnValueOnce(events({
        event: 'error', data: { code: 'TEMPORARY', message: '稍后重试', request_id: 'req-old' },
      }))
      .mockReturnValueOnce(events(
        { event: 'token', data: { delta: '重试成功' } },
        {
          event: 'done', data: {
            request_id: 'req-new', citations: [], retrieved_chunk_count: 0,
            timings: { rewrite_ms: 0, retrieval_ms: 0, generation_ms: 1, total_ms: 1 },
          },
        },
      ))
    const store = useConversationsStore()
    store.activate('u-1', 'kb-1')
    await store.submit('问题')
    const failedId = store.messages[store.messages.length - 1]?.id
    const statuses: (string | null)[] = []
    const stopWatching = watch(
      () => {
        const answer = store.messages.find((item) => item.id === failedId)
        return answer?.kind === 'assistant' ? answer.status : null
      },
      (status) => statuses.push(status),
      { flush: 'sync' },
    )

    await store.retry(failedId!)
    stopWatching()

    expect(store.messages.filter((item) => item.kind === 'user')).toHaveLength(1)
    expect(store.messages).toHaveLength(2)
    expect(store.messages[store.messages.length - 1]).toMatchObject({ status: 'completed', content: '重试成功' })
    expect(statuses).toContain('completed')
  })

  it('清理用户时取消该用户的等待保存并删除其全部会话', async () => {
    vi.useFakeTimers()
    vi.mocked(streamQuestion).mockImplementation(async function* () {
      yield { event: 'token', data: { delta: '待清理' } }
      await new Promise<void>(() => {})
    })
    const store = useConversationsStore()
    store.activate('u-1', 'kb-1')
    void store.submit('问题')
    await Promise.resolve()
    await Promise.resolve()
    expect(store.messages[store.messages.length - 1]).toMatchObject({ content: '待清理' })
    sessionStorage.setItem(conversationStorageKey('u-1', 'kb-2'), '[]')
    sessionStorage.setItem(conversationStorageKey('u-2', 'kb-1'), '[]')

    store.clearUser('u-1')
    await vi.advanceTimersByTimeAsync(200)

    expect(sessionStorage.getItem(conversationStorageKey('u-1', 'kb-1'))).toBeNull()
    expect(sessionStorage.getItem(conversationStorageKey('u-1', 'kb-2'))).toBeNull()
    expect(sessionStorage.getItem(conversationStorageKey('u-2', 'kb-1'))).not.toBeNull()
  })

  it('停止后忽略已缓冲的完成事件', async () => {
    let release!: () => void
    vi.mocked(streamQuestion).mockImplementation(async function* () {
      yield { event: 'token', data: { delta: '已收到的片段' } }
      await new Promise<void>((resolve) => { release = resolve })
      yield {
        event: 'done', data: {
          request_id: 'req-late', citations: [], retrieved_chunk_count: 0,
          timings: { rewrite_ms: 0, retrieval_ms: 0, generation_ms: 1, total_ms: 1 },
        },
      }
    })
    const store = useConversationsStore()
    store.activate('u-1', 'kb-1')
    const pending = store.submit('问题')
    await vi.waitFor(() => expect(store.messages[store.messages.length - 1]).toMatchObject({ content: '已收到的片段' }))

    store.stop()
    release()
    await pending

    expect(store.messages[store.messages.length - 1]).toMatchObject({
      status: 'stopped', content: '已收到的片段', requestId: null,
    })
  })

  it('旧流结束时不会覆盖重新激活知识库后的新会话', async () => {
    let releaseOld!: () => void
    vi.mocked(streamQuestion)
      .mockImplementationOnce(async function* () {
        yield { event: 'token', data: { delta: '旧片段' } }
        await new Promise<void>((resolve) => { releaseOld = resolve })
        yield {
          event: 'done', data: {
            request_id: 'req-old', citations: [], retrieved_chunk_count: 0,
            timings: { rewrite_ms: 0, retrieval_ms: 0, generation_ms: 1, total_ms: 1 },
          },
        }
      })
      .mockReturnValueOnce(events({
        event: 'done', data: {
          request_id: 'req-new', citations: [], retrieved_chunk_count: 0,
          timings: { rewrite_ms: 0, retrieval_ms: 0, generation_ms: 1, total_ms: 1 },
        },
      }))
    const store = useConversationsStore()
    store.activate('u-1', 'kb-1')
    const oldPending = store.submit('旧问题')
    await vi.waitFor(() => expect(store.messages[store.messages.length - 1]).toMatchObject({ content: '旧片段' }))

    store.activate('u-1', 'kb-2')
    store.activate('u-1', 'kb-1')
    await store.submit('新问题')
    releaseOld()
    await oldPending

    expect(sessionStorage.getItem(conversationStorageKey('u-1', 'kb-1'))).toContain('req-new')
  })
})

import { createPinia, setActivePinia } from 'pinia'
import { watch } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/conversations', () => ({
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
  getConversation: vi.fn(),
  listConversations: vi.fn(),
  streamConversationMessage: vi.fn(),
}))

import { ApiError } from '../api/client'
import {
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
  streamConversationMessage,
} from '../api/conversations'
import type {
  ConversationDetail,
  ConversationSummary,
  ServerConversationMessage,
} from '../types/api'
import type { QuestionStreamEvent } from '../types/conversation'
import { conversationStorageKey } from './conversationStorage'
import { useConversationsStore } from './conversations'

const conversation = (
  id: string,
  knowledgeBaseId = 'kb-1',
): ConversationSummary => ({
  id,
  knowledge_base_id: knowledgeBaseId,
  title: `会话 ${id}`,
  created_at: '2026-07-20T08:00:00Z',
  updated_at: '2026-07-20T08:00:00Z',
})

const message = (
  id: string,
  sequenceNumber: number,
  role: 'user' | 'assistant',
  content: string,
  overrides: Partial<ServerConversationMessage> = {},
): ServerConversationMessage => ({
  id,
  sequence_number: sequenceNumber,
  role,
  content,
  status: role === 'user' ? 'completed' : 'completed',
  retry_of_message_id: null,
  citations_snapshot: [],
  retrieval_stats: {},
  timings: {},
  finish_reason: role === 'assistant' ? 'stop' : null,
  error_code: null,
  created_at: '2026-07-20T08:00:00Z',
  completed_at: '2026-07-20T08:00:01Z',
  ...overrides,
})

const detail = (
  id: string,
  messages: ServerConversationMessage[] = [],
  knowledgeBaseId = 'kb-1',
): ConversationDetail => ({ ...conversation(id, knowledgeBaseId), messages })

async function* events(...items: QuestionStreamEvent[]) {
  for (const item of items) yield item
}

function last<T>(items: T[]): T | undefined {
  return items[items.length - 1]
}

const done = (requestId = 'req-1'): QuestionStreamEvent => ({
  event: 'done',
  data: {
    request_id: requestId,
    citations: [],
    retrieved_chunk_count: 0,
    timings: { rewrite_ms: 0, retrieval_ms: 0, generation_ms: 1, total_ms: 1 },
  },
})

describe('服务端会话 Store', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    setActivePinia(createPinia())
    sessionStorage.clear()
    vi.mocked(listConversations).mockResolvedValue({
      items: [conversation('conversation-1')], page: 1, page_size: 20, total: 1,
    })
    vi.mocked(getConversation).mockResolvedValue(detail('conversation-1'))
    vi.mocked(createConversation).mockResolvedValue(conversation('conversation-new'))
    vi.mocked(deleteConversation).mockResolvedValue(undefined)
    vi.mocked(streamConversationMessage).mockReturnValue(events(done()))
  })

  it('从服务端加载列表和详情，不导入旧浏览器历史并只清理当前用户旧键', async () => {
    const currentKey = conversationStorageKey('u-1', 'kb-1')
    const otherKey = conversationStorageKey('u-2', 'kb-1')
    sessionStorage.setItem(currentKey, '[{"content":"不得导入"}]')
    sessionStorage.setItem(otherKey, '[{"content":"其他用户"}]')
    vi.mocked(getConversation).mockResolvedValue(detail('conversation-1', [
      message('message-user-1', 1, 'user', '服务端问题'),
      message('message-assistant-1', 2, 'assistant', '服务端回答'),
    ]))
    const store = useConversationsStore()

    await store.activate('u-1', 'kb-1')

    expect(listConversations).toHaveBeenCalledWith('kb-1', { page: 1, pageSize: 20 })
    expect(getConversation).toHaveBeenCalledWith('conversation-1')
    expect(store.activeConversationId).toBe('conversation-1')
    expect(store.messages.map((item) => item.id)).toEqual([
      'message-user-1', 'message-assistant-1',
    ])
    expect(store.messages.flatMap((item) => item.kind === 'divider' ? [] : [item.content]))
      .not.toContain('不得导入')
    expect(sessionStorage.getItem(currentKey)).toBeNull()
    expect(sessionStorage.getItem(otherKey)).toContain('其他用户')
  })

  it('新建会话使用服务端 conversation ID 并可删除后回到剩余会话', async () => {
    const first = conversation('conversation-1')
    const created = conversation('conversation-2')
    vi.mocked(listConversations).mockResolvedValue({
      items: [first], page: 1, page_size: 20, total: 1,
    })
    vi.mocked(createConversation).mockResolvedValue(created)
    vi.mocked(getConversation).mockResolvedValue(detail('conversation-1'))
    const store = useConversationsStore()
    await store.activate('u-1', 'kb-1')

    await store.newConversation()
    expect(createConversation).toHaveBeenCalledWith('kb-1')
    expect(store.activeConversationId).toBe('conversation-2')
    expect(store.messages).toEqual([])

    await store.deleteConversation('conversation-2')
    expect(deleteConversation).toHaveBeenCalledWith('conversation-2')
    expect(store.activeConversationId).toBe('conversation-1')
    expect(getConversation).toHaveBeenLastCalledWith('conversation-1')
  })

  it('连续新建会话会复用同一个服务端创建请求', async () => {
    let resolveCreate!: (value: ConversationSummary) => void
    vi.mocked(createConversation).mockReturnValue(new Promise((resolve) => {
      resolveCreate = resolve
    }))
    const store = useConversationsStore()
    await store.activate('u-1', 'kb-1')

    const first = store.newConversation()
    const second = store.newConversation()
    expect(store.creating).toBe(true)
    expect(createConversation).toHaveBeenCalledOnce()
    resolveCreate(conversation('conversation-2'))
    await Promise.all([first, second])

    expect(store.creating).toBe(false)
    expect(store.activeConversationId).toBe('conversation-2')
  })

  it('无活动会话时双提交只创建并启动一条消息流', async () => {
    let resolveCreate!: (value: ConversationSummary) => void
    vi.mocked(listConversations).mockResolvedValue({
      items: [], page: 1, page_size: 20, total: 0,
    })
    vi.mocked(createConversation).mockReturnValue(new Promise((resolve) => {
      resolveCreate = resolve
    }))
    vi.mocked(getConversation).mockResolvedValue(detail('conversation-2', [
      message('message-user-1', 1, 'user', '问题一'),
      message('message-assistant-1', 2, 'assistant', '回答一'),
    ]))
    vi.mocked(streamConversationMessage).mockReturnValue(events(done('request-one')))
    const store = useConversationsStore()
    await store.activate('u-1', 'kb-1')

    const first = store.submit('问题一')
    await expect(store.submit('问题二')).rejects.toThrow('当前回答尚未结束。')
    resolveCreate(conversation('conversation-2'))
    await first

    expect(createConversation).toHaveBeenCalledOnce()
    expect(streamConversationMessage).toHaveBeenCalledOnce()
    expect(streamConversationMessage).toHaveBeenCalledWith(
      'conversation-2', { question: '问题一' }, expect.any(AbortSignal),
    )
  })

  it('创建进行中清空会等待创建落库后再删除该会话', async () => {
    let resolveCreate!: (value: ConversationSummary) => void
    const created = conversation('conversation-created')
    vi.mocked(createConversation).mockReturnValue(new Promise((resolve) => {
      resolveCreate = resolve
    }))
    vi.mocked(listConversations).mockResolvedValue({
      items: [created], page: 1, page_size: 100, total: 1,
    })
    const store = useConversationsStore()
    store.activeUserId = 'u-1'
    store.activeKnowledgeBaseId = 'kb-1'

    const creating = store.newConversation()
    const clearing = store.clear()
    resolveCreate(created)
    await Promise.all([creating, clearing])

    expect(deleteConversation).toHaveBeenCalledWith('conversation-created')
    expect(store.activeConversationId).toBeNull()
    expect(store.conversations).toEqual([])
  })

  it('清空历史会分页取得全部服务端会话后逐一删除', async () => {
    vi.mocked(listConversations)
      .mockResolvedValueOnce({
        items: [conversation('conversation-1')], page: 1, page_size: 20, total: 1,
      })
      .mockResolvedValueOnce({
        items: [conversation('conversation-1')], page: 1, page_size: 100, total: 2,
      })
      .mockResolvedValueOnce({
        items: [conversation('conversation-2')], page: 2, page_size: 100, total: 2,
      })
    const store = useConversationsStore()
    await store.activate('u-1', 'kb-1')

    await store.clear()

    expect(listConversations).toHaveBeenNthCalledWith(2, 'kb-1', { page: 1, pageSize: 100 })
    expect(listConversations).toHaveBeenNthCalledWith(3, 'kb-1', { page: 2, pageSize: 100 })
    expect(vi.mocked(deleteConversation).mock.calls.map(([id]) => id)).toEqual([
      'conversation-1', 'conversation-2',
    ])
    expect(store.conversations).toEqual([])
    expect(store.activeConversationId).toBeNull()
  })

  it('清空中途失败会保留已成功删除的本地进度，之后可以继续', async () => {
    const first = conversation('conversation-1')
    const second = conversation('conversation-2')
    vi.mocked(listConversations)
      .mockResolvedValueOnce({ items: [first, second], page: 1, page_size: 20, total: 2 })
      .mockResolvedValueOnce({ items: [first, second], page: 1, page_size: 100, total: 2 })
      .mockResolvedValueOnce({ items: [second], page: 1, page_size: 100, total: 1 })
    vi.mocked(deleteConversation)
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new ApiError(503, 'SERVICE_UNAVAILABLE', '暂不可用'))
      .mockResolvedValueOnce(undefined)
    const store = useConversationsStore()
    await store.activate('u-1', 'kb-1')

    await expect(store.clear()).rejects.toMatchObject({ code: 'SERVICE_UNAVAILABLE' })
    expect(store.conversations.map((item) => item.id)).toEqual(['conversation-2'])
    expect(store.activeConversationId).toBeNull()

    await store.clear()
    expect(store.conversations).toEqual([])
    expect(vi.mocked(deleteConversation).mock.calls.map(([id]) => id)).toEqual([
      'conversation-1', 'conversation-2', 'conversation-2',
    ])
  })

  it('正常流完成后以服务端消息 ID 和终态详情覆盖临时流状态', async () => {
    vi.mocked(getConversation)
      .mockResolvedValueOnce(detail('conversation-1'))
      .mockResolvedValueOnce(detail('conversation-1', [
        message('message-user-1', 1, 'user', '问题'),
        message('message-assistant-1', 2, 'assistant', '答案'),
      ]))
    vi.mocked(streamConversationMessage).mockReturnValue(events(
      { event: 'token', data: { delta: '答案' } },
      done('request-complete'),
    ))
    const store = useConversationsStore()
    await store.activate('u-1', 'kb-1')

    await store.submit('问题')

    expect(streamConversationMessage).toHaveBeenCalledWith(
      'conversation-1', { question: '问题' }, expect.any(AbortSignal),
    )
    expect(store.messages.map((item) => item.id)).toEqual([
      'message-user-1', 'message-assistant-1',
    ])
    expect(store.messages[1]).toMatchObject({
      status: 'completed', content: '答案', usageStatus: 'unknown',
    })
    expect(sessionStorage.length).toBe(0)
  })

  it('建流前 HTTP 拒绝不会把旧终态回答误认成本次新消息', async () => {
    const history = [
      message('message-user-old', 1, 'user', '旧问题'),
      message('message-assistant-old', 2, 'assistant', '旧回答'),
    ]
    vi.mocked(getConversation).mockResolvedValue(detail('conversation-1', history))
    vi.mocked(streamConversationMessage).mockImplementation(async function* () {
      throw new ApiError(429, 'QUESTION_RATE_LIMITED', '问答请求过于频繁。')
    })
    const store = useConversationsStore()
    await store.activate('u-1', 'kb-1')
    const observedStatuses: string[] = []
    const stopWatching = watch(
      () => {
        const current = last(store.messages)
        return current?.kind === 'assistant' ? current.status : null
      },
      (status) => { if (status) observedStatuses.push(status) },
      { flush: 'sync' },
    )

    await store.submit('新问题')
    stopWatching()

    expect(store.messages.slice(0, 2).map((item) => item.id)).toEqual([
      'message-user-old', 'message-assistant-old',
    ])
    expect(last(store.messages)).toMatchObject({
      id: expect.stringMatching(/^pending:/),
      status: 'failed', errorCode: 'QUESTION_RATE_LIMITED', retryMode: 'question',
    })
    expect(observedStatuses).toContain('failed')
    expect(store.messages.some(
      (item) => item.kind === 'user' && item.content === '新问题',
    )).toBe(true)
  })

  it('用户停止后取得服务端消息 ID，并可用该 ID 手动重试', async () => {
    const stopped = [
      message('message-user-1', 1, 'user', '问题'),
      message('message-assistant-stopped', 2, 'assistant', '部分回答', {
        status: 'interrupted', error_code: 'STREAM_CANCELED', finish_reason: null,
      }),
    ]
    const retried = [
      ...stopped,
      message('message-assistant-retry', 3, 'assistant', '重试回答', {
        retry_of_message_id: 'message-assistant-stopped',
      }),
    ]
    vi.mocked(getConversation)
      .mockResolvedValueOnce(detail('conversation-1'))
      .mockResolvedValueOnce(detail('conversation-1', stopped))
      .mockResolvedValueOnce(detail('conversation-1', retried))
    vi.mocked(streamConversationMessage)
      .mockImplementationOnce(async function* (_conversationId, _input, signal) {
        yield { event: 'token', data: { delta: '部分回答' } }
        await new Promise<void>((_resolve, reject) => signal.addEventListener('abort', () => {
          reject(new DOMException('aborted', 'AbortError'))
        }))
      })
      .mockReturnValueOnce(events(done('request-retry-after-stop')))
    const store = useConversationsStore()
    await store.activate('u-1', 'kb-1')
    const pending = store.submit('问题')
    await vi.waitFor(() => expect(last(store.messages)).toMatchObject({ content: '部分回答' }))

    store.stop()
    await pending

    expect(last(store.messages)).toMatchObject({
      id: 'message-assistant-stopped', status: 'stopped',
      failureKind: 'user_stopped', errorCode: null,
    })

    await store.retry('message-assistant-stopped')
    expect(streamConversationMessage).toHaveBeenLastCalledWith(
      'conversation-1', { retry_of_message_id: 'message-assistant-stopped' },
      expect.any(AbortSignal),
    )
    expect(last(store.messages)).toMatchObject({
      id: 'message-assistant-retry', status: 'completed',
    })
  })

  it('网络断开保留部分内容并标记 interrupted，不伪装成用户停止', async () => {
    vi.mocked(streamConversationMessage).mockImplementation(async function* () {
      yield { event: 'token', data: { delta: '断线前部分' } }
      throw new TypeError('network stream closed')
    })
    vi.mocked(getConversation)
      .mockResolvedValueOnce(detail('conversation-1'))
      .mockResolvedValueOnce(detail('conversation-1', [
        message('message-user-1', 1, 'user', '问题'),
        message('message-assistant-1', 2, 'assistant', '断线前部分', {
          status: 'streaming', completed_at: null, finish_reason: null,
        }),
      ]))
      .mockResolvedValueOnce(detail('conversation-1', [
        message('message-user-1', 1, 'user', '问题'),
        message('message-assistant-1', 2, 'assistant', '断线前部分', {
          status: 'interrupted', error_code: 'CLIENT_DISCONNECTED', finish_reason: null,
        }),
      ]))
    const store = useConversationsStore()
    await store.activate('u-1', 'kb-1')

    await store.submit('问题')

    expect(last(store.messages)).toMatchObject({
      id: 'message-assistant-1', status: 'interrupted', content: '断线前部分',
      failureKind: 'server', errorCode: 'CLIENT_DISCONNECTED', usageStatus: 'unknown',
    })
  })

  it('流式回答期间删除另一会话不会中断当前 SSE', async () => {
    let release!: () => void
    let signal!: AbortSignal
    const first = conversation('conversation-1')
    const second = conversation('conversation-2')
    vi.mocked(listConversations).mockResolvedValue({
      items: [first, second], page: 1, page_size: 20, total: 2,
    })
    vi.mocked(getConversation)
      .mockResolvedValueOnce(detail('conversation-1'))
      .mockResolvedValueOnce(detail('conversation-1', [
        message('message-user-1', 1, 'user', '问题'),
        message('message-assistant-1', 2, 'assistant', '回答'),
      ]))
    vi.mocked(streamConversationMessage).mockImplementation(async function* (
      _conversationId, _input, runSignal,
    ) {
      signal = runSignal
      yield { event: 'token', data: { delta: '回答' } }
      await new Promise<void>((resolve) => { release = resolve })
      yield done('request-current')
    })
    const store = useConversationsStore()
    await store.activate('u-1', 'kb-1')
    const pending = store.submit('问题')
    await vi.waitFor(() => expect(last(store.messages)).toMatchObject({ content: '回答' }))

    await store.deleteConversation('conversation-2')
    expect(signal.aborted).toBe(false)
    expect(last(store.messages)).toMatchObject({ status: 'streaming' })
    release()
    await pending
    expect(last(store.messages)).toMatchObject({ id: 'message-assistant-1', status: 'completed' })
  })

  it('加载服务端 interrupted 消息并保留服务端错误原因', async () => {
    vi.mocked(getConversation).mockResolvedValue(detail('conversation-1', [
      message('message-user-1', 1, 'user', '问题'),
      message('message-assistant-1', 2, 'assistant', '部分回答', {
        status: 'interrupted', error_code: 'CLIENT_DISCONNECTED', finish_reason: null,
      }),
    ]))
    const store = useConversationsStore()

    await store.activate('u-1', 'kb-1')

    expect(last(store.messages)).toMatchObject({
      id: 'message-assistant-1', status: 'interrupted',
      failureKind: 'server', errorCode: 'CLIENT_DISCONNECTED', usageStatus: 'unknown',
    })
  })

  it('手动重试发送服务端失败消息 ID，且不重复追加用户问题', async () => {
    const original = [
      message('message-user-1', 1, 'user', '问题'),
      message('message-assistant-old', 2, 'assistant', '失败部分', {
        status: 'failed', error_code: 'CHAT_PROVIDER_ERROR', finish_reason: null,
      }),
    ]
    const retried = [
      ...original,
      message('message-assistant-new', 3, 'assistant', '重试成功', {
        retry_of_message_id: 'message-assistant-old',
      }),
    ]
    vi.mocked(getConversation)
      .mockResolvedValueOnce(detail('conversation-1', original))
      .mockResolvedValueOnce(detail('conversation-1', retried))
    vi.mocked(streamConversationMessage).mockReturnValue(events(done('request-retry')))
    const store = useConversationsStore()
    await store.activate('u-1', 'kb-1')

    await store.retry('message-assistant-old')

    expect(streamConversationMessage).toHaveBeenCalledWith(
      'conversation-1', { retry_of_message_id: 'message-assistant-old' },
      expect.any(AbortSignal),
    )
    expect(store.messages.filter((item) => item.kind === 'user')).toHaveLength(1)
    expect(last(store.messages)).toMatchObject({
      id: 'message-assistant-new', questionId: 'message-user-1', status: 'completed',
    })
  })

  it('旧 SSE 的迟到事件不能覆盖切换后的新知识库会话', async () => {
    let releaseOld!: () => void
    vi.mocked(listConversations).mockImplementation(async (knowledgeBaseId) => ({
      items: [conversation(`conversation-${knowledgeBaseId}`, knowledgeBaseId)],
      page: 1, page_size: 20, total: 1,
    }))
    vi.mocked(getConversation).mockImplementation(async (conversationId) => {
      const knowledgeBaseId = conversationId.endsWith('kb-2') ? 'kb-2' : 'kb-1'
      return detail(conversationId, knowledgeBaseId === 'kb-2' ? [
        message('message-kb-2-user', 1, 'user', '新知识库问题'),
        message('message-kb-2-answer', 2, 'assistant', '新知识库回答'),
      ] : [], knowledgeBaseId)
    })
    vi.mocked(streamConversationMessage).mockImplementation(async function* () {
      yield { event: 'token', data: { delta: '旧片段' } }
      await new Promise<void>((resolve) => { releaseOld = resolve })
      yield done('request-old')
    })
    const store = useConversationsStore()
    await store.activate('u-1', 'kb-1')
    const oldPending = store.submit('旧问题')
    await vi.waitFor(() => expect(last(store.messages)).toMatchObject({ content: '旧片段' }))

    await store.activate('u-1', 'kb-2')
    releaseOld()
    await oldPending

    expect(store.activeKnowledgeBaseId).toBe('kb-2')
    expect(store.activeConversationId).toBe('conversation-kb-2')
    expect(store.messages.map((item) => item.id)).toEqual([
      'message-kb-2-user', 'message-kb-2-answer',
    ])
  })

  it('建流前断网且服务端不存在消息时可用原问题手动重新生成', async () => {
    let detailCall = 0
    const completed = detail('conversation-1', [
      message('message-user-retry', 1, 'user', '问题'),
      message('message-assistant-retry', 2, 'assistant', '恢复后的回答'),
    ])
    vi.mocked(getConversation).mockImplementation(async () => {
      detailCall += 1
      return detailCall <= 7 ? detail('conversation-1') : completed
    })
    vi.mocked(streamConversationMessage)
      .mockImplementationOnce(async function* () {
        throw new TypeError('network unavailable before response')
      })
      .mockReturnValueOnce(events(done('request-after-network-recovery')))
    const store = useConversationsStore()
    await store.activate('u-1', 'kb-1')

    await store.submit('问题')
    const interrupted = last(store.messages)
    expect(interrupted).toMatchObject({
      id: expect.stringMatching(/^pending:/), status: 'interrupted', retryMode: 'question',
    })

    await store.retry(interrupted!.id)

    expect(streamConversationMessage).toHaveBeenCalledTimes(2)
    expect(last(store.messages)).toMatchObject({
      id: 'message-assistant-retry', status: 'completed', content: '恢复后的回答',
    })
  })

  it('服务端仍在结算时手动重新生成会给出明确提示且不重复请求', async () => {
    vi.mocked(streamConversationMessage).mockImplementation(async function* () {
      yield { event: 'token', data: { delta: '部分回答' } }
      throw new TypeError('network stream closed')
    })
    const streaming = detail('conversation-1', [
      message('message-user-1', 1, 'user', '问题'),
      message('message-assistant-1', 2, 'assistant', '部分回答', {
        status: 'streaming', completed_at: null, finish_reason: null,
      }),
    ])
    vi.mocked(getConversation)
      .mockResolvedValueOnce(detail('conversation-1'))
      .mockResolvedValue(streaming)
    const store = useConversationsStore()
    await store.activate('u-1', 'kb-1')
    await store.submit('问题')
    const interrupted = last(store.messages)

    await expect(store.retry(interrupted!.id)).rejects.toThrow('回答仍在服务端处理中')
    expect(streamConversationMessage).toHaveBeenCalledOnce()
  })

  it('服务端从处理中转为中断后使用服务端消息 ID 重新生成', async () => {
    const streaming = detail('conversation-1', [
      message('message-user-1', 1, 'user', '问题'),
      message('message-assistant-old', 2, 'assistant', '部分回答', {
        status: 'streaming', completed_at: null, finish_reason: null,
      }),
    ])
    const interrupted = detail('conversation-1', [
      message('message-user-1', 1, 'user', '问题'),
      message('message-assistant-old', 2, 'assistant', '部分回答', {
        status: 'interrupted', error_code: 'CLIENT_DISCONNECTED', finish_reason: null,
      }),
    ])
    const retried = detail('conversation-1', [
      ...interrupted.messages,
      message('message-assistant-new', 3, 'assistant', '重新生成成功', {
        retry_of_message_id: 'message-assistant-old',
      }),
    ])
    let detailCall = 0
    vi.mocked(getConversation).mockImplementation(async () => {
      detailCall += 1
      if (detailCall === 1) return detail('conversation-1')
      if (detailCall <= 7) return streaming
      if (detailCall === 8) return interrupted
      return retried
    })
    vi.mocked(streamConversationMessage)
      .mockImplementationOnce(async function* () {
        yield { event: 'token', data: { delta: '部分回答' } }
        throw new TypeError('network stream closed')
      })
      .mockReturnValueOnce(events(done('request-retried')))
    const store = useConversationsStore()
    await store.activate('u-1', 'kb-1')
    await store.submit('问题')
    const pendingAnswerId = last(store.messages)!.id

    await expect(store.retry(pendingAnswerId)).rejects.toThrow('回答仍在服务端处理中')
    await store.retry(pendingAnswerId)

    expect(streamConversationMessage).toHaveBeenLastCalledWith(
      'conversation-1', { retry_of_message_id: 'message-assistant-old' },
      expect.any(AbortSignal),
    )
    expect(last(store.messages)).toMatchObject({
      id: 'message-assistant-new', status: 'completed', content: '重新生成成功',
    })
  })

  it('历史引用快照字段不完整时会规范化为安全占位值', async () => {
    vi.mocked(getConversation).mockResolvedValue(detail('conversation-1', [
      message('message-user-1', 1, 'user', '问题'),
      message('message-assistant-1', 2, 'assistant', '回答', {
        citations_snapshot: [{ citation_id: 3, file_name: '简版快照.md' }],
      }),
    ]))
    const store = useConversationsStore()

    await store.activate('u-1', 'kb-1')

    expect(last(store.messages)).toMatchObject({
      citations: [{
        citation_id: 3,
        file_name: '简版快照.md',
        content: '引用快照正文不可用。',
        relevance_score: null,
      }],
    })
  })

  it('会话详情切换失败时恢复原活动会话和消息', async () => {
    const originalMessages = [
      message('message-user-1', 1, 'user', '原问题'),
      message('message-assistant-1', 2, 'assistant', '原回答'),
    ]
    vi.mocked(listConversations).mockResolvedValue({
      items: [conversation('conversation-1'), conversation('conversation-2')],
      page: 1, page_size: 20, total: 2,
    })
    vi.mocked(getConversation)
      .mockResolvedValueOnce(detail('conversation-1', originalMessages))
      .mockRejectedValueOnce(new ApiError(404, 'CONVERSATION_NOT_FOUND', '会话不存在。'))
    const store = useConversationsStore()
    await store.activate('u-1', 'kb-1')

    await expect(store.openConversation('conversation-2')).rejects.toMatchObject({
      code: 'CONVERSATION_NOT_FOUND',
    })

    expect(store.activeConversationId).toBe('conversation-1')
    expect(store.messages.map((item) => item.id)).toEqual([
      'message-user-1', 'message-assistant-1',
    ])
  })

  it('加载更多会话时追加并去重，切换知识库后重置页码', async () => {
    vi.mocked(listConversations)
      .mockResolvedValueOnce({
        items: [conversation('conversation-1')], page: 1, page_size: 20, total: 2,
      })
      .mockResolvedValueOnce({
        items: [conversation('conversation-1'), conversation('conversation-2')],
        page: 2, page_size: 20, total: 2,
      })
      .mockResolvedValueOnce({
        items: [conversation('conversation-kb-2', 'kb-2')], page: 1, page_size: 20, total: 1,
      })
    vi.mocked(getConversation).mockImplementation(async (conversationId) => detail(
      conversationId,
      [],
      conversationId === 'conversation-kb-2' ? 'kb-2' : 'kb-1',
    ))
    const store = useConversationsStore()
    await store.activate('u-1', 'kb-1')

    await store.loadConversations(2)
    expect(store.conversations.map((item) => item.id)).toEqual([
      'conversation-1', 'conversation-2',
    ])
    expect(store.currentPage).toBe(2)

    await store.activate('u-1', 'kb-2')
    expect(store.currentPage).toBe(1)
    expect(store.conversations.map((item) => item.id)).toEqual(['conversation-kb-2'])
  })
})

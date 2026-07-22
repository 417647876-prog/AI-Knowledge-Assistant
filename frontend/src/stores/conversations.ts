import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { ApiError } from '../api/client'
import {
  createConversation as createConversationRequest,
  deleteConversation as deleteConversationRequest,
  getConversation,
  listConversations,
  streamConversationMessage,
} from '../api/conversations'
import { isAbortError } from '../api/questions'
import type {
  Citation,
  ConversationDetail,
  ConversationSummary,
  ServerConversationMessage,
} from '../types/api'
import type {
  AssistantMessage,
  AssistantStatus,
  ConversationMessage,
  StreamFailureKind,
  StreamTimings,
} from '../types/conversation'
import { clearLegacyConversationsForUser } from './conversationStorage'

const PAGE_SIZE = 20
type ReconcileResult = 'terminal' | 'pending' | 'missing' | 'stale'

function numberField(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function stringField(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function citationsFromSnapshot(values: Record<string, unknown>[]): Citation[] {
  return values.flatMap((value, index) => {
    if (!value || typeof value !== 'object') return []
    return [{
      citation_id: numberField(value.citation_id) ?? index + 1,
      document_id: stringField(value.document_id) ?? '',
      file_name: stringField(value.file_name)?.trim() || '未知来源',
      content: stringField(value.content) ?? '引用快照正文不可用。',
      relevance_score: numberField(value.relevance_score),
      page_number: numberField(value.page_number),
      sheet_name: stringField(value.sheet_name),
      row_start: numberField(value.row_start),
      section_title: stringField(value.section_title),
    }]
  })
}

function timingsOf(value: Record<string, unknown>): StreamTimings | null {
  const rewrite = numberField(value.rewrite_ms)
  const retrieval = numberField(value.retrieval_ms)
  const generation = numberField(value.generation_ms)
  const total = numberField(value.total_ms)
  if (rewrite === null && retrieval === null && generation === null && total === null) return null
  return {
    rewrite_ms: rewrite ?? 0,
    retrieval_ms: retrieval ?? 0,
    generation_ms: generation ?? 0,
    total_ms: total ?? 0,
  }
}

function assistantStatusOf(message: ServerConversationMessage): AssistantStatus {
  if (message.status === 'completed') return 'completed'
  if (message.status === 'interrupted') return 'interrupted'
  if (message.status === 'failed') return 'failed'
  return 'streaming'
}

function failureKindOf(message: ServerConversationMessage): StreamFailureKind | null {
  return message.status === 'interrupted' || message.status === 'failed' ? 'server' : null
}

function messagesFromServer(detail: ConversationDetail): ConversationMessage[] {
  const result: ConversationMessage[] = []
  const assistantQuestions = new Map<string, string>()
  let latestUserId: string | null = null

  for (const message of detail.messages) {
    if (message.role === 'user') {
      latestUserId = message.id
      result.push({
        id: message.id,
        kind: 'user',
        content: message.content,
        createdAt: message.created_at,
      })
      continue
    }

    const retriedQuestionId = message.retry_of_message_id
      ? assistantQuestions.get(message.retry_of_message_id)
      : null
    const questionId = retriedQuestionId ?? latestUserId
    if (!questionId) continue
    assistantQuestions.set(message.id, questionId)
    result.push({
      id: message.id,
      kind: 'assistant',
      questionId,
      content: message.content,
      createdAt: message.created_at,
      status: assistantStatusOf(message),
      phase: null,
      citations: citationsFromSnapshot(message.citations_snapshot),
      standaloneQuestion: null,
      retrievedChunkCount: numberField(message.retrieval_stats.retrieved_chunk_count),
      timings: timingsOf(message.timings),
      errorCode: message.error_code,
      requestId: null,
      failureKind: failureKindOf(message),
      // 会话详情不返回逐条 usage；缺失时必须保持未知，不能伪造为 0。
      usageStatus: 'unknown',
    })
  }
  return result
}

export const useConversationsStore = defineStore('conversations', () => {
  const conversations = ref<ConversationSummary[]>([])
  const messages = ref<ConversationMessage[]>([])
  const activeUserId = ref<string | null>(null)
  const activeKnowledgeBaseId = ref<string | null>(null)
  const activeConversationId = ref<string | null>(null)
  const total = ref(0)
  const currentPage = ref(1)
  const loading = ref(false)
  const creating = ref(false)
  const submitting = ref(false)
  const clearing = ref(false)
  const isStreaming = computed(() => messages.value.some(
    (item) => item.kind === 'assistant' && item.status === 'streaming',
  ))

  let controller: AbortController | null = null
  let stateGeneration = 0
  let activeStreamGeneration: number | null = null
  let listLoadSequence = 0
  let newConversationPromise: Promise<void> | null = null
  let newConversationKnowledgeBaseId: string | null = null
  let clearPromise: Promise<void> | null = null

  function streamingAnswer(): AssistantMessage | undefined {
    return [...messages.value].reverse().find(
      (item): item is AssistantMessage => item.kind === 'assistant' && item.status === 'streaming',
    )
  }

  function serverAssistantIds(): Set<string> {
    return new Set(messages.value.flatMap((item) =>
      item.kind === 'assistant' && !item.id.startsWith('pending:') ? [item.id] : [],
    ))
  }

  function cancelForStateChange(): number {
    const answer = streamingAnswer()
    if (answer) Object.assign(answer, {
      status: 'interrupted',
      phase: null,
      failureKind: 'server',
      errorCode: 'STREAM_CANCELED',
      usageStatus: 'unknown',
    })
    stateGeneration += 1
    activeStreamGeneration = null
    controller?.abort()
    controller = null
    return stateGeneration
  }

  async function applyDetail(conversationId: string, generation: number): Promise<boolean> {
    const detail = await getConversation(conversationId)
    if (generation !== stateGeneration || activeConversationId.value !== conversationId) return false
    messages.value = messagesFromServer(detail)
    const index = conversations.value.findIndex((item) => item.id === detail.id)
    if (index >= 0) conversations.value[index] = {
      id: detail.id,
      knowledge_base_id: detail.knowledge_base_id,
      title: detail.title,
      created_at: detail.created_at,
      updated_at: detail.updated_at,
    }
    return true
  }

  async function reconcileAfterStream(
    conversationId: string,
    generation: number,
    stoppedByUser = false,
    baselineAssistantIds: ReadonlySet<string> | null = null,
  ): Promise<ReconcileResult> {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const detail = await getConversation(conversationId)
      if (generation !== stateGeneration || activeConversationId.value !== conversationId) return 'stale'
      const serverMessages = messagesFromServer(detail)
      const latestAssistant = [...serverMessages].reverse().find(
        (item): item is AssistantMessage => item.kind === 'assistant'
          && (!baselineAssistantIds || !baselineAssistantIds.has(item.id)),
      )
      // 断开后服务端可能仍在结算。不能用无 controller 的 streaming 覆盖本地终态。
      if (!latestAssistant || latestAssistant.status === 'streaming') {
        if (attempt < 2) {
          await new Promise((resolve) => setTimeout(resolve, 25 * (attempt + 1)))
          continue
        }
        return latestAssistant ? 'pending' : 'missing'
      }
      messages.value = serverMessages
      if (stoppedByUser) Object.assign(latestAssistant, {
        status: 'stopped',
        failureKind: 'user_stopped',
        errorCode: null,
      })
      const index = conversations.value.findIndex((item) => item.id === detail.id)
      if (index >= 0) conversations.value[index] = {
        id: detail.id,
        knowledge_base_id: detail.knowledge_base_id,
        title: detail.title,
        created_at: detail.created_at,
        updated_at: detail.updated_at,
      }
      return 'terminal'
    }
    return 'missing'
  }

  async function loadConversations(page = 1, pageSize = PAGE_SIZE): Promise<void> {
    const knowledgeBaseId = activeKnowledgeBaseId.value
    if (!knowledgeBaseId) return
    const generation = stateGeneration
    const sequence = ++listLoadSequence
    loading.value = true
    try {
      const result = await listConversations(knowledgeBaseId, { page, pageSize })
      if (
        generation !== stateGeneration
        || activeKnowledgeBaseId.value !== knowledgeBaseId
        || sequence !== listLoadSequence
      ) return
      conversations.value = page === 1
        ? result.items
        : [
            ...conversations.value,
            ...result.items.filter((item) => !conversations.value.some(
              (current) => current.id === item.id,
            )),
          ]
      total.value = result.total
      currentPage.value = page
    } finally {
      if (generation === stateGeneration && sequence === listLoadSequence) loading.value = false
    }
  }

  async function openConversation(conversationId: string): Promise<void> {
    const generation = cancelForStateChange()
    const previousConversationId = activeConversationId.value
    const previousMessages = messages.value
    activeConversationId.value = conversationId
    messages.value = []
    try {
      await applyDetail(conversationId, generation)
    } catch (error) {
      if (generation === stateGeneration && activeConversationId.value === conversationId) {
        activeConversationId.value = previousConversationId
        messages.value = previousMessages
      }
      throw error
    }
  }

  async function activate(
    userId: string,
    knowledgeBaseId: string,
    force = false,
  ): Promise<void> {
    if (
      !force && activeUserId.value === userId
      && activeKnowledgeBaseId.value === knowledgeBaseId
      && (activeConversationId.value || loading.value)
    ) return

    const generation = cancelForStateChange()
    activeUserId.value = userId
    activeKnowledgeBaseId.value = knowledgeBaseId
    activeConversationId.value = null
    conversations.value = []
    messages.value = []
    total.value = 0
    currentPage.value = 1
    clearLegacyConversationsForUser(userId)
    loading.value = true
    try {
      const result = await listConversations(knowledgeBaseId, { page: 1, pageSize: PAGE_SIZE })
      if (generation !== stateGeneration) return
      conversations.value = result.items
      total.value = result.total
      currentPage.value = 1
      const first = result.items[0]
      if (!first) return
      activeConversationId.value = first.id
      await applyDetail(first.id, generation)
    } finally {
      if (generation === stateGeneration) loading.value = false
    }
  }

  async function newConversation(): Promise<void> {
    if (clearPromise) await clearPromise
    const knowledgeBaseId = activeKnowledgeBaseId.value
    if (!knowledgeBaseId) throw new Error('请先选择知识库。')
    if (newConversationPromise && newConversationKnowledgeBaseId === knowledgeBaseId) {
      return newConversationPromise
    }
    const previousConversationId = activeConversationId.value
    const baselineAssistantIds = serverAssistantIds()
    const generation = cancelForStateChange()
    const operation = (async () => {
      try {
        const created = await createConversationRequest(knowledgeBaseId)
        if (generation !== stateGeneration || activeKnowledgeBaseId.value !== knowledgeBaseId) return
        conversations.value = [created, ...conversations.value.filter((item) => item.id !== created.id)]
        total.value += 1
        activeConversationId.value = created.id
        messages.value = []
      } catch (error) {
        if (
          previousConversationId
          && generation === stateGeneration
          && activeConversationId.value === previousConversationId
        ) {
          await reconcileAfterStream(
            previousConversationId, generation, false, baselineAssistantIds,
          ).catch((): ReconcileResult => 'stale')
        }
        throw error
      }
    })()
    newConversationPromise = operation
    newConversationKnowledgeBaseId = knowledgeBaseId
    creating.value = true
    try {
      await operation
    } finally {
      if (newConversationPromise === operation) {
        newConversationPromise = null
        newConversationKnowledgeBaseId = null
        creating.value = false
      }
    }
  }

  async function ensureConversation(): Promise<string> {
    if (!activeConversationId.value) await newConversation()
    if (!activeConversationId.value) throw new Error('创建会话失败。')
    return activeConversationId.value
  }

  function makePendingAnswer(
    id: string,
    questionId: string,
  ): AssistantMessage {
    return {
      id,
      kind: 'assistant',
      questionId,
      content: '',
      createdAt: new Date().toISOString(),
      status: 'streaming',
      phase: null,
      citations: [],
      standaloneQuestion: null,
      retrievedChunkCount: null,
      timings: null,
      errorCode: null,
      requestId: null,
      failureKind: null,
      usageStatus: 'unknown',
      retryMode: 'message',
    }
  }

  async function consume(
    conversationId: string,
    input: { question: string } | { retry_of_message_id: string },
    answer: AssistantMessage,
  ): Promise<void> {
    const generation = stateGeneration + 1
    stateGeneration = generation
    activeStreamGeneration = generation
    const runController = new AbortController()
    controller = runController
    let terminal = false
    let stoppedByUser = false
    let receivedStreamEvent = false
    const baselineAssistantIds = serverAssistantIds()

    try {
      for await (const event of streamConversationMessage(conversationId, input, runController.signal)) {
        receivedStreamEvent = true
        if (
          generation !== stateGeneration
          || activeConversationId.value !== conversationId
          || runController.signal.aborted
        ) break
        if (event.event === 'status') answer.phase = event.data.phase
        if (event.event === 'rewrite') {
          answer.standaloneQuestion = event.data.standalone_question
          answer.rewriteUsedFallback = event.data.used_fallback
          answer.timings = {
            rewrite_ms: event.data.elapsed_ms,
            retrieval_ms: 0,
            generation_ms: 0,
            total_ms: 0,
          }
        }
        if (event.event === 'retrieval') {
          answer.retrievedChunkCount = event.data.retrieved_chunk_count
          answer.timings = {
            rewrite_ms: answer.timings?.rewrite_ms ?? 0,
            retrieval_ms: event.data.elapsed_ms,
            generation_ms: 0,
            total_ms: 0,
          }
        }
        if (event.event === 'token') answer.content += event.data.delta
        if (event.event === 'citation' && !answer.citations.some(
          (item) => item.citation_id === event.data.citation_id,
        )) answer.citations.push(event.data)
        if (event.event === 'done') {
          Object.assign(answer, {
            status: 'completed',
            phase: null,
            citations: event.data.citations,
            retrievedChunkCount: event.data.retrieved_chunk_count,
            timings: event.data.timings,
            requestId: event.data.request_id,
            failureKind: null,
            usageStatus: event.data.usage_complete === true ? 'known' : 'unknown',
          })
          terminal = true
          break
        }
        if (event.event === 'error') {
          Object.assign(answer, {
            status: 'failed',
            phase: null,
            errorCode: event.data.code,
            requestId: event.data.request_id,
            failureKind: 'server',
            usageStatus: 'unknown',
          })
          terminal = true
          break
        }
      }
      if (
        !terminal
        && runController.signal.aborted
        && generation === stateGeneration
        && activeConversationId.value === conversationId
      ) {
        terminal = true
        stoppedByUser = answer.failureKind === 'user_stopped'
      }
    } catch (error) {
      if (generation !== stateGeneration) {
        // 用户停止或会话切换已先行更新状态，迟到异常不能覆盖新状态。
      } else if (isAbortError(error)) {
        Object.assign(answer, {
          status: 'stopped', phase: null, failureKind: 'user_stopped', errorCode: null,
        })
        terminal = true
        stoppedByUser = true
      } else {
        const serverHttpFailure = error instanceof ApiError && error.status > 0
        Object.assign(answer, {
          status: serverHttpFailure ? 'failed' : 'interrupted',
          phase: null,
          failureKind: !(error instanceof ApiError) || error.status === 0
            ? 'network'
            : 'server',
          errorCode: error instanceof ApiError ? error.code : 'STREAM_INTERRUPTED',
          requestId: error instanceof ApiError ? error.requestId ?? null : null,
          usageStatus: 'unknown',
          retryMode: serverHttpFailure || !receivedStreamEvent ? 'question' : 'message',
        })
        terminal = true
      }
    } finally {
      if (controller === runController) controller = null
      if (activeStreamGeneration === generation) activeStreamGeneration = null
    }

    if (!terminal || generation !== stateGeneration || activeConversationId.value !== conversationId) return
    try {
      await reconcileAfterStream(
        conversationId, generation, stoppedByUser, baselineAssistantIds,
      )
    } catch {
      // 断网时保留已收到的部分内容和明确状态，恢复后可手动重新加载服务端详情。
    }
  }

  async function submit(content: string): Promise<void> {
    if (submitting.value || isStreaming.value) throw new Error('当前回答尚未结束。')
    if (clearPromise) throw new Error('正在清空会话，请稍后再试。')
    const question = content.trim()
    if (!question) return
    submitting.value = true
    try {
      const conversationId = await ensureConversation()
      if (isStreaming.value) throw new Error('当前回答尚未结束。')
      const nextGeneration = stateGeneration + 1
      const questionId = `pending:${conversationId}:${nextGeneration}:user`
      const answerId = `pending:${conversationId}:${nextGeneration}:assistant`
      messages.value.push({
        id: questionId,
        kind: 'user',
        content: question,
        createdAt: new Date().toISOString(),
      })
      const answer = makePendingAnswer(answerId, questionId)
      messages.value.push(answer)
      await consume(conversationId, { question }, answer)
    } finally {
      submitting.value = false
    }
  }

  function stop(): void {
    const answer = streamingAnswer()
    if (answer) Object.assign(answer, {
      status: 'stopped',
      phase: null,
      failureKind: 'user_stopped',
      errorCode: null,
      usageStatus: 'unknown',
    })
    controller?.abort()
    controller = null
  }

  async function retry(answerId: string): Promise<void> {
    if (isStreaming.value) throw new Error('当前回答尚未结束。')
    const conversationId = activeConversationId.value
    if (!conversationId) return
    let old = messages.value.find(
      (item): item is AssistantMessage => item.kind === 'assistant' && item.id === answerId,
    )
    if (!old || !['failed', 'interrupted', 'stopped'].includes(old.status)) return
    if (answerId.startsWith('pending:')) {
      const pendingAnswer = old
      const stoppedByUser = pendingAnswer.failureKind === 'user_stopped'
      const baselineAssistantIds = serverAssistantIds()
      const reconcileResult = await reconcileAfterStream(
        conversationId, stateGeneration, stoppedByUser, baselineAssistantIds,
      ).catch((): ReconcileResult => 'stale')
      if (reconcileResult !== 'terminal') {
        if (reconcileResult === 'pending') {
          throw new Error('回答仍在服务端处理中，请稍后再试。')
        }
        if (pendingAnswer.retryMode !== 'question' || reconcileResult === 'stale') {
          throw new Error('回答尚未完成结算，请重新加载会话后再试。')
        }
        const question = messages.value.find(
          (item) => item.kind === 'user' && item.id === pendingAnswer.questionId,
        )
        if (!question || question.kind !== 'user') {
          throw new Error('原问题不可用，请重新输入问题。')
        }
        messages.value = messages.value.filter(
          (item) => item.id !== pendingAnswer.id && item.id !== question.id,
        )
        await submit(question.content)
        return
      }
      old = [...messages.value].reverse().find(
        (item): item is AssistantMessage => item.kind === 'assistant'
          && !baselineAssistantIds.has(item.id)
          && ['failed', 'interrupted', 'stopped'].includes(item.status),
      )
      if (!old) return
      answerId = old.id
    }
    const nextGeneration = stateGeneration + 1
    const answer = makePendingAnswer(
      `pending:${conversationId}:${nextGeneration}:assistant`,
      old.questionId,
    )
    messages.value.push(answer)
    await consume(conversationId, { retry_of_message_id: answerId }, answer)
  }

  async function deleteConversation(conversationId: string): Promise<void> {
    const wasActive = activeConversationId.value === conversationId
    const baselineAssistantIds = serverAssistantIds()
    const generation = wasActive ? cancelForStateChange() : stateGeneration
    try {
      await deleteConversationRequest(conversationId)
    } catch (error) {
      if (wasActive && generation === stateGeneration) {
        await reconcileAfterStream(
          conversationId, generation, false, baselineAssistantIds,
        ).catch((): ReconcileResult => 'stale')
      }
      throw error
    }
    if (generation !== stateGeneration) return
    conversations.value = conversations.value.filter((item) => item.id !== conversationId)
    total.value = Math.max(0, total.value - 1)
    if (!wasActive) return
    const next = conversations.value[0]
    activeConversationId.value = next?.id ?? null
    messages.value = []
    if (next) await applyDetail(next.id, generation)
  }

  async function performClear(): Promise<void> {
    const pendingCreation = newConversationPromise
    if (pendingCreation) await pendingCreation
    const knowledgeBaseId = activeKnowledgeBaseId.value
    if (!knowledgeBaseId) return
    const generation = cancelForStateChange()
    const ids: string[] = []
    let page = 1
    while (true) {
      const result = await listConversations(knowledgeBaseId, { page, pageSize: 100 })
      if (generation !== stateGeneration || activeKnowledgeBaseId.value !== knowledgeBaseId) return
      ids.push(...result.items.map((item) => item.id))
      if (!result.items.length || ids.length >= result.total) break
      page += 1
    }
    for (const conversationId of ids) {
      try {
        await deleteConversationRequest(conversationId)
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 404) throw error
      }
      if (generation !== stateGeneration) return
      conversations.value = conversations.value.filter((item) => item.id !== conversationId)
      total.value = Math.max(0, total.value - 1)
      if (activeConversationId.value === conversationId) {
        activeConversationId.value = null
        messages.value = []
      }
    }
    conversations.value = []
    messages.value = []
    activeConversationId.value = null
    total.value = 0
    currentPage.value = 1
  }

  async function clear(): Promise<void> {
    if (clearPromise) return clearPromise
    if (submitting.value || isStreaming.value) throw new Error('当前回答尚未结束。')
    const operation = performClear()
    clearPromise = operation
    clearing.value = true
    try {
      await operation
    } finally {
      if (clearPromise === operation) {
        clearPromise = null
        clearing.value = false
      }
    }
  }

  function clearUser(userId: string): void {
    clearLegacyConversationsForUser(userId)
    if (activeUserId.value !== userId) return
    cancelForStateChange()
    conversations.value = []
    messages.value = []
    activeConversationId.value = null
    activeKnowledgeBaseId.value = null
    activeUserId.value = null
    total.value = 0
    currentPage.value = 1
    loading.value = false
    creating.value = false
    submitting.value = false
    clearing.value = false
  }

  return {
    conversations,
    messages,
    activeUserId,
    activeKnowledgeBaseId,
    activeConversationId,
    total,
    currentPage,
    loading,
    creating,
    submitting,
    clearing,
    isStreaming,
    activate,
    loadConversations,
    openConversation,
    submit,
    stop,
    retry,
    newConversation,
    deleteConversation,
    clear,
    clearUser,
  }
})

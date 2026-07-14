import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { ApiError } from '../api/client'
import { streamQuestion } from '../api/questions'
import {
  buildHistory,
  clearUserConversations,
  loadConversation,
  saveConversation,
  trimConversation,
} from './conversationStorage'
import type { AssistantMessage, ConversationMessage } from '../types/conversation'

const SAVE_DELAY_MS = 200

export const useConversationsStore = defineStore('conversations', () => {
  const messages = ref<ConversationMessage[]>([])
  const activeUserId = ref<string | null>(null)
  const activeKnowledgeBaseId = ref<string | null>(null)
  const isStreaming = computed(() => messages.value.some(
    (item) => item.kind === 'assistant' && item.status === 'streaming',
  ))

  let controller: AbortController | null = null
  let streamGeneration = 0
  const pendingSaves = new Map<string, ReturnType<typeof setTimeout>>()

  const saveKey = (userId: string, knowledgeBaseId: string) => `${userId}:${knowledgeBaseId}`

  function saveNow(userId: string, knowledgeBaseId: string, snapshot: ConversationMessage[]) {
    const key = saveKey(userId, knowledgeBaseId)
    const pending = pendingSaves.get(key)
    if (pending) clearTimeout(pending)
    pendingSaves.delete(key)
    saveConversation(userId, knowledgeBaseId, snapshot)
  }

  function scheduleSave(userId: string, knowledgeBaseId: string, snapshot: ConversationMessage[]) {
    const key = saveKey(userId, knowledgeBaseId)
    const pending = pendingSaves.get(key)
    if (pending) clearTimeout(pending)
    const timer = setTimeout(() => {
      saveConversation(userId, knowledgeBaseId, snapshot)
      if (pendingSaves.get(key) === timer) pendingSaves.delete(key)
    }, SAVE_DELAY_MS)
    pendingSaves.set(key, timer)
  }

  function persistActive() {
    if (activeUserId.value && activeKnowledgeBaseId.value)
      saveNow(activeUserId.value, activeKnowledgeBaseId.value, messages.value)
  }

  function activate(userId: string, knowledgeBaseId: string) {
    stop()
    persistActive()
    activeUserId.value = userId
    activeKnowledgeBaseId.value = knowledgeBaseId
    messages.value = loadConversation(userId, knowledgeBaseId)
  }

  function isAbortError(error: unknown): boolean {
    return error instanceof DOMException && error.name === 'AbortError'
  }

  async function consume(questionId: string, question: string, answer: AssistantMessage) {
    const runUserId = activeUserId.value
    const runKnowledgeBaseId = activeKnowledgeBaseId.value
    if (!runUserId || !runKnowledgeBaseId) throw new Error('请先选择知识库。')

    const runMessages = messages.value
    const runGeneration = streamGeneration
    const runController = new AbortController()
    controller = runController

    try {
      const history = buildHistory(runMessages, questionId)
      for await (const event of streamQuestion(
        runKnowledgeBaseId, question, history, runController.signal,
      )) {
        if (runGeneration !== streamGeneration || runController.signal.aborted) break
        if (event.event === 'status') answer.phase = event.data.phase
        if (event.event === 'rewrite') {
          answer.standaloneQuestion = event.data.standalone_question
          answer.timings = {
            rewrite_ms: event.data.elapsed_ms, retrieval_ms: 0, generation_ms: 0, total_ms: 0,
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
            status: 'completed', phase: null, citations: event.data.citations,
            retrievedChunkCount: event.data.retrieved_chunk_count,
            timings: event.data.timings, requestId: event.data.request_id,
          })
          break
        }
        if (event.event === 'error') {
          Object.assign(answer, {
            status: 'failed', phase: null, errorCode: event.data.code,
            requestId: event.data.request_id,
          })
          break
        }
        scheduleSave(runUserId, runKnowledgeBaseId, runMessages)
      }
    } catch (error) {
      if (isAbortError(error)) answer.status = 'stopped'
      else {
        answer.status = 'failed'
        answer.errorCode = error instanceof ApiError ? error.code : 'STREAM_ERROR'
        answer.requestId = error instanceof ApiError ? error.requestId ?? null : null
      }
      answer.phase = null
    } finally {
      if (controller === runController) controller = null
      if (runGeneration === streamGeneration && !runController.signal.aborted)
        saveNow(runUserId, runKnowledgeBaseId, runMessages)
    }
  }

  async function submit(content: string) {
    if (isStreaming.value) throw new Error('当前回答尚未结束。')
    const question = {
      id: crypto.randomUUID(), kind: 'user' as const,
      content: content.trim(), createdAt: new Date().toISOString(),
    }
    const answer: AssistantMessage = {
      id: crypto.randomUUID(), kind: 'assistant', questionId: question.id,
      content: '', createdAt: new Date().toISOString(), status: 'streaming', phase: null,
      citations: [], standaloneQuestion: null, retrievedChunkCount: null, timings: null,
      errorCode: null, requestId: null,
    }
    messages.value = trimConversation([...messages.value, question, answer])
    persistActive()
    const reactiveAnswer = messages.value.find(
      (item): item is AssistantMessage => item.kind === 'assistant' && item.id === answer.id,
    )
    if (!reactiveAnswer) throw new Error('未找到当前回答。')
    await consume(question.id, question.content, reactiveAnswer)
  }

  function stop() {
    streamGeneration += 1
    const activeAnswer = [...messages.value].reverse().find(
      (item): item is AssistantMessage => item.kind === 'assistant' && item.status === 'streaming',
    )
    if (activeAnswer) {
      activeAnswer.status = 'stopped'
      activeAnswer.phase = null
    }
    controller?.abort()
    persistActive()
  }

  async function retry(answerId: string) {
    const index = messages.value.findIndex((item) => item.id === answerId)
    const old = messages.value[index]
    if (!old || old.kind !== 'assistant' || old.status !== 'failed') return
    const question = messages.value.find((item) => item.kind === 'user' && item.id === old.questionId)
    if (!question || question.kind !== 'user') return

    const replacement: AssistantMessage = {
      ...old, content: '', status: 'streaming', phase: null, citations: [], standaloneQuestion: null,
      retrievedChunkCount: null, timings: null, errorCode: null, requestId: null,
    }
    messages.value[index] = replacement
    persistActive()
    const reactiveReplacement = messages.value[index]
    if (!reactiveReplacement || reactiveReplacement.kind !== 'assistant') return
    await consume(question.id, question.content, reactiveReplacement)
  }

  function newConversation() {
    stop()
    messages.value.push({
      id: crypto.randomUUID(), kind: 'divider', createdAt: new Date().toISOString(),
    })
    persistActive()
  }

  function clear() {
    stop()
    messages.value = []
    persistActive()
  }

  function clearUser(userId: string) {
    for (const [key, timer] of pendingSaves) {
      if (!key.startsWith(`${userId}:`)) continue
      clearTimeout(timer)
      pendingSaves.delete(key)
    }
    if (activeUserId.value === userId) {
      stop()
      messages.value = []
    }
    clearUserConversations(userId)
  }

  return {
    messages, activeUserId, activeKnowledgeBaseId, isStreaming,
    activate, submit, stop, retry, newConversation, clear, clearUser,
  }
})

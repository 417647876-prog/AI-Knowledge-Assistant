import type { Citation } from './api'

export type ConversationRole = 'user' | 'assistant'

export interface ConversationHistory {
  role: ConversationRole
  content: string
}

export interface StreamTimings {
  rewrite_ms: number
  retrieval_ms: number
  generation_ms: number
  total_ms: number
}

export type AssistantStatus = 'streaming' | 'completed' | 'stopped' | 'failed'

export interface UserMessage {
  id: string
  kind: 'user'
  content: string
  createdAt: string
}

export interface AssistantMessage {
  id: string
  kind: 'assistant'
  questionId: string
  content: string
  createdAt: string
  status: AssistantStatus
  phase: 'rewriting' | 'retrieving' | 'generating' | null
  citations: Citation[]
  standaloneQuestion: string | null
  rewriteUsedFallback?: boolean
  retrievedChunkCount: number | null
  timings: StreamTimings | null
  errorCode: string | null
  requestId: string | null
}

export interface DividerMessage {
  id: string
  kind: 'divider'
  createdAt: string
}

export type ConversationMessage = UserMessage | AssistantMessage | DividerMessage

export type QuestionStreamEvent =
  | { event: 'status'; data: { phase: 'rewriting' | 'retrieving' | 'generating' } }
  | {
      event: 'rewrite'
      data: {
        standalone_question: string
        elapsed_ms: number
        used_fallback: boolean
      }
    }
  | { event: 'retrieval'; data: { retrieved_chunk_count: number; elapsed_ms: number } }
  | { event: 'token'; data: { delta: string } }
  | { event: 'citation'; data: Citation }
  | { event: 'done'; data: { request_id: string; citations: Citation[]; retrieved_chunk_count: number; timings: StreamTimings } }
  | { event: 'error'; data: { code: string; message: string; request_id: string } }

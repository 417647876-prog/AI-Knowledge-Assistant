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

export type QuestionStreamEvent =
  | { event: 'status'; data: { phase: 'rewriting' | 'retrieving' | 'generating' } }
  | { event: 'rewrite'; data: { standalone_question: string; elapsed_ms: number } }
  | { event: 'retrieval'; data: { retrieved_chunk_count: number; elapsed_ms: number } }
  | { event: 'token'; data: { delta: string } }
  | { event: 'citation'; data: Citation }
  | { event: 'done'; data: { request_id: string; citations: Citation[]; retrieved_chunk_count: number; timings: StreamTimings } }
  | { event: 'error'; data: { code: string; message: string; request_id: string } }

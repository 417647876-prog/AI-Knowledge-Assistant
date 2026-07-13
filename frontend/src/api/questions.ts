import type { QuestionResponse } from '../types/api'
import { apiRequest } from './client'

export const askQuestion = (knowledgeBaseId: string, question: string, topK = 5) =>
  apiRequest<QuestionResponse>(`/api/v1/knowledge-bases/${knowledgeBaseId}/questions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, top_k: topK }),
  })

import type { KnowledgeBase } from '../types/api'
import { apiRequest } from './client'

export interface CreateKnowledgeBaseInput { name: string; description: string | null }
export const listKnowledgeBases = () => apiRequest<KnowledgeBase[]>('/api/v1/knowledge-bases')
export const createKnowledgeBase = (input: CreateKnowledgeBaseInput) =>
  apiRequest<KnowledgeBase>('/api/v1/knowledge-bases', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input),
  })
export const deleteKnowledgeBase = (knowledgeBaseId: string) =>
  apiRequest<void>(`/api/v1/knowledge-bases/${knowledgeBaseId}`, { method: 'DELETE' })

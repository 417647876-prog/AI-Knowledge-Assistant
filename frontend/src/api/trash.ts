import type { PurgeJobResponse, TrashResponse } from '../types/api'
import { apiRequest } from './client'

export const listTrash = () => apiRequest<TrashResponse>('/api/v1/trash')

export const restoreTrashKnowledgeBase = (knowledgeBaseId: string) =>
  apiRequest<void>(`/api/v1/knowledge-bases/${knowledgeBaseId}/restore`, { method: 'POST' })

export const purgeTrashKnowledgeBase = (knowledgeBaseId: string) =>
  apiRequest<PurgeJobResponse>(`/api/v1/knowledge-bases/${knowledgeBaseId}/purge`, {
    method: 'DELETE',
  })

export const restoreTrashDocument = (documentId: string) =>
  apiRequest<void>(`/api/v1/documents/${documentId}/restore`, { method: 'POST' })

export const purgeTrashDocument = (documentId: string) =>
  apiRequest<PurgeJobResponse>(`/api/v1/documents/${documentId}/purge`, { method: 'DELETE' })

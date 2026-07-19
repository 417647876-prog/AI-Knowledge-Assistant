import type { SupportAdministrator, SupportGrant } from '../types/api'
import { apiRequest } from './client'

export interface SupportGrantInput {
  admin_user_id: string
  expires_in_minutes?: number
}

const jsonHeaders = { 'Content-Type': 'application/json' }

export const listSupportAdministrators = () =>
  apiRequest<SupportAdministrator[]>('/api/v1/support-administrators')

export function createSupportGrant(
  knowledgeBaseId: string,
  input: SupportGrantInput,
): Promise<SupportGrant> {
  return apiRequest<SupportGrant>(
    `/api/v1/knowledge-bases/${knowledgeBaseId}/support-grants`,
    { method: 'POST', headers: jsonHeaders, body: JSON.stringify(input) },
  )
}

export const listSupportGrants = (knowledgeBaseId: string) =>
  apiRequest<SupportGrant[]>(`/api/v1/knowledge-bases/${knowledgeBaseId}/support-grants`)

export const revokeSupportGrant = (grantId: string) =>
  apiRequest<void>(`/api/v1/support-grants/${grantId}`, { method: 'DELETE' })

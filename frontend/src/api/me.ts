import type {
  FeedbackItem,
  FeedbackPage,
  FeedbackReason,
  PageOptions,
  QuotaResponse,
  UsageSummary,
} from '../types/api'
import { apiRequest } from './client'

export interface FeedbackInput {
  helpful: boolean
  reason?: FeedbackReason | null
}

const jsonHeaders = { 'Content-Type': 'application/json' }

const serializeDate = (value: string | Date) =>
  typeof value === 'string' ? value : value.toISOString()

export const getMyQuota = () => apiRequest<QuotaResponse>('/api/v1/me/quota')

export function getMyUsage(from: string | Date, to: string | Date): Promise<UsageSummary> {
  const query = new URLSearchParams({ from: serializeDate(from), to: serializeDate(to) })
  return apiRequest<UsageSummary>(`/api/v1/me/usage?${query}`)
}

export function listMyFeedback(options: PageOptions = {}): Promise<FeedbackPage> {
  const query = new URLSearchParams()
  query.set('page', String(options.page ?? 1))
  query.set('page_size', String(options.pageSize ?? 20))
  return apiRequest<FeedbackPage>(`/api/v1/me/feedback?${query}`)
}

export function putMessageFeedback(
  messageId: string,
  input: FeedbackInput,
): Promise<FeedbackItem> {
  return apiRequest<FeedbackItem>(`/api/v1/messages/${messageId}/feedback`, {
    method: 'PUT', headers: jsonHeaders, body: JSON.stringify(input),
  })
}

export const removeMessageFeedback = (messageId: string) =>
  apiRequest<void>(`/api/v1/messages/${messageId}/feedback`, { method: 'DELETE' })

import type {
  OperationsJobsResponse,
  OperationsOverview,
  OperationsQuality,
  UserOperationsSummary,
} from '../types/api'
import { apiRequest } from './client'

export interface OperationsTimeRange {
  startAt?: string | Date
  endAt?: string | Date
}

export interface OperationsJobsQuery extends OperationsTimeRange {
  limit?: number
  cursorCreatedAt?: string | Date
  cursorId?: string
}

const serializeDate = (value: string | Date) =>
  typeof value === 'string' ? value : value.toISOString()

function operationsQuery(
  range: OperationsTimeRange,
  extra: Record<string, string | number | Date | undefined> = {},
): string {
  const query = new URLSearchParams()
  if (range.startAt) query.set('start_at', serializeDate(range.startAt))
  if (range.endAt) query.set('end_at', serializeDate(range.endAt))
  for (const [key, value] of Object.entries(extra)) {
    if (value !== undefined) query.set(key, value instanceof Date ? value.toISOString() : String(value))
  }
  const suffix = query.toString()
  return suffix ? `?${suffix}` : ''
}

export const getOperationsOverview = (range: OperationsTimeRange = {}) =>
  apiRequest<OperationsOverview>(`/api/v1/admin/operations/overview${operationsQuery(range)}`)

export const listUserOperations = (range: OperationsTimeRange = {}) =>
  apiRequest<UserOperationsSummary[]>(`/api/v1/admin/operations/users${operationsQuery(range)}`)

export function getOperationsJobs(query: OperationsJobsQuery = {}): Promise<OperationsJobsResponse> {
  return apiRequest<OperationsJobsResponse>(
    `/api/v1/admin/operations/jobs${operationsQuery(query, {
      limit: query.limit,
      cursor_created_at: query.cursorCreatedAt,
      cursor_id: query.cursorId,
    })}`,
  )
}

export const getOperationsQuality = (range: OperationsTimeRange = {}) =>
  apiRequest<OperationsQuality>(`/api/v1/admin/operations/quality${operationsQuery(range)}`)

import type { AdminQuota, AdminUser, UserRole } from '../types/api'
import { apiRequest } from './client'

export interface AdminUserCreateInput {
  username: string
  password: string
  role: UserRole
}

export interface AdminUserUpdateInput {
  role?: UserRole
  is_active?: boolean
}
export type AdminQuotaInput = AdminQuota

const jsonHeaders = { 'Content-Type': 'application/json' }

export function listAdminUsers(): Promise<AdminUser[]> {
  return apiRequest<AdminUser[]>('/api/v1/admin/users')
}

export function createAdminUser(input: AdminUserCreateInput): Promise<AdminUser> {
  return apiRequest<AdminUser>('/api/v1/admin/users', {
    method: 'POST', headers: jsonHeaders, body: JSON.stringify(input),
  })
}

export function updateAdminUser(
  userId: string,
  input: AdminUserUpdateInput,
): Promise<AdminUser> {
  return apiRequest<AdminUser>(`/api/v1/admin/users/${userId}`, {
    method: 'PATCH', headers: jsonHeaders, body: JSON.stringify(input),
  })
}

export function resetAdminUserPassword(userId: string, password: string): Promise<AdminUser> {
  return apiRequest<AdminUser>(`/api/v1/admin/users/${userId}/reset-password`, {
    method: 'POST', headers: jsonHeaders, body: JSON.stringify({ password }),
  })
}

export function getAdminUserQuota(userId: string): Promise<AdminQuota> {
  return apiRequest<AdminQuota>(`/api/v1/admin/users/${userId}/quota`)
}

export function updateAdminUserQuota(
  userId: string,
  input: AdminQuotaInput,
): Promise<AdminQuota> {
  return apiRequest<AdminQuota>(`/api/v1/admin/users/${userId}/quota`, {
    method: 'PUT', headers: jsonHeaders, body: JSON.stringify(input),
  })
}

import type { AuthSession } from '../types/api'
import { apiRequest } from './client'

const publicAuthOptions = { authenticated: false, retryUnauthorized: false }

export function login(username: string, password: string): Promise<AuthSession> {
  return apiRequest<AuthSession>('/api/v1/auth/login', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  }, publicAuthOptions)
}

export function refresh(): Promise<AuthSession> {
  return apiRequest<AuthSession>('/api/v1/auth/refresh', {
    method: 'POST', credentials: 'include',
  }, publicAuthOptions)
}

export function logout(): Promise<void> {
  return apiRequest<void>('/api/v1/auth/logout', {
    method: 'POST', credentials: 'include',
  }, publicAuthOptions)
}

export type DocumentStatus = 'pending' | 'parsing' | 'embedding' | 'ready' | 'failed'
export type UserRole = 'admin' | 'user'
export interface CurrentUser {
  id: string
  username: string
  role: UserRole
  is_active: boolean
}
export interface AuthSession {
  access_token: string
  token_type: 'bearer'
  expires_in: number
  user: CurrentUser
}
export interface AdminUser extends CurrentUser {
  created_at: string
  updated_at: string
}
export interface KnowledgeBase {
  id: string
  name: string
  description: string | null
  owner_id: string
  owner_username: string
}
export interface DocumentTask {
  document_id: string; job_id: string; status: DocumentStatus
  error_code: string | null; error_message: string | null; file_name: string
}
export interface DocumentListResponse {
  items: DocumentTask[]
}
export interface Citation {
  citation_id: number; document_id: string; file_name: string; content: string
  relevance_score: number; page_number: number | null; sheet_name: string | null
  row_start: number | null; section_title: string | null
}
export interface QuestionResponse {
  answer: string; citations: Citation[]; retrieved_chunk_count: number; request_id: string
}
export interface ApiErrorEnvelope {
  error?: { code?: string; message?: string; request_id?: string }
}

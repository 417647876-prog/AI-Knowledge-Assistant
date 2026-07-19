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

export interface PageOptions {
  page?: number
  pageSize?: number
}

export interface ConversationSummary {
  id: string
  knowledge_base_id: string
  title: string
  created_at: string
  updated_at: string
}

export interface ConversationPage {
  items: ConversationSummary[]
  page: number
  page_size: number
  total: number
}

export type ServerConversationMessageStatus =
  | 'streaming'
  | 'completed'
  | 'interrupted'
  | 'failed'

export interface ServerConversationMessage {
  id: string
  sequence_number: number
  role: 'user' | 'assistant'
  content: string
  status: ServerConversationMessageStatus
  retry_of_message_id: string | null
  citations_snapshot: Record<string, unknown>[]
  retrieval_stats: Record<string, unknown>
  timings: Record<string, unknown>
  finish_reason: string | null
  error_code: string | null
  created_at: string
  completed_at: string | null
}

export interface ConversationDetail extends ConversationSummary {
  messages: ServerConversationMessage[]
}

export interface QuotaValues {
  daily_question_limit: number
  daily_upload_limit: number
  storage_bytes_limit: number
}

export interface QuotaOverrides {
  daily_question_limit: number | null
  daily_upload_limit: number | null
  storage_bytes_limit: number | null
}

export interface QuotaResponse {
  defaults: QuotaValues
  overrides: QuotaOverrides
  used: { question_count: number; upload_count: number; storage_bytes_used: number }
  remaining: { question_count: number; upload_count: number; storage_bytes: number }
}

export interface TokenSummary {
  cache_hit_input_tokens: number
  cache_miss_input_tokens: number
  output_tokens: number
  reasoning_tokens: number
  total_tokens: number
}

export interface PurposeUsageSummary {
  event_count: number
  total_tokens: number
  estimated_cost: string
  usage_unknown_count: number
}

export interface UsageSummary {
  from: string
  to: string
  tokens: TokenSummary
  estimated_cost: string
  usage_unknown_count: number
  purposes: Record<string, PurposeUsageSummary>
}

export type FeedbackReason =
  | 'helpful_clear'
  | 'helpful_cited'
  | 'unhelpful_wrong'
  | 'unhelpful_missing'
  | 'unhelpful_unclear'

export interface FeedbackItem {
  id: string
  message_id: string
  helpful: boolean
  reason: FeedbackReason | null
  created_at: string
  updated_at: string
}

export interface FeedbackPage {
  items: FeedbackItem[]
  page: number
  page_size: number
  total: number
}

export interface TrashKnowledgeBase {
  id: string
  name: string
  deleted_at: string
  purge_after: string
}

export interface TrashDocument {
  id: string
  knowledge_base_id: string
  file_name: string
  deleted_at: string
  purge_after: string
}

export interface TrashResponse {
  knowledge_bases: TrashKnowledgeBase[]
  documents: TrashDocument[]
}

export interface PurgeJobResponse {
  job_id: string
  status: string
}

export interface SupportGrant {
  id: string
  knowledge_base_id: string
  admin_user_id: string
  access_level: string
  expires_at: string
  revoked_at: string | null
  created_at: string
  last_used_at: string | null
}

export interface OperationsOverview {
  account_total: number
  active_account_total: number
  knowledge_base_total: number
  document_total: number
  effective_document_bytes: number
  job_status_counts: Record<string, number>
  token_total: number
  cost_total: string
  feedback: Record<string, number>
  risk_event_total: number
  system_health: Record<string, unknown>
}

export interface UserOperationsSummary {
  user_id: string
  username: string
  role: string
  is_active: boolean
  knowledge_base_total: number
  document_total: number
  effective_document_bytes: number
  job_total: number
  token_total: number
  cost_total: string
}

export interface OperationsJob {
  id: string
  resource_type: string
  status: string
  stage: string | null
  attempt_count: number
  error_code: string | null
  created_at: string
}

export interface OperationsJobCursor {
  created_at: string
  id: string
}

export interface OperationsJobsResponse {
  items: OperationsJob[]
  next_cursor: OperationsJobCursor | null
}

export interface OfflineEvaluationSummary {
  mode: string
  gate_passed: boolean
  started_at: string
  completed_at: string
  duration_ms: number
}

export interface OperationsQuality {
  latest_offline_evaluation: OfflineEvaluationSummary | null
  online_agent_metrics: Record<string, number>
  feedback_distribution: Record<string, number>
}

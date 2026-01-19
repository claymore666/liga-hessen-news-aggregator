import api from './client'
import type { Source, SourceCreate, Channel, ChannelCreate, Item, Rule, Stats, SourceStats, ChannelStats, PaginatedResponse, ItemEvent } from '@/types'

export const sourcesApi = {
  list: (params?: { enabled?: boolean; has_errors?: boolean }) =>
    api.get<Source[]>('/sources', { params }),
  listWithErrors: () => api.get<Source[]>('/sources/errors'),
  get: (id: number) => api.get<Source>(`/sources/${id}`),
  create: (data: SourceCreate) => api.post<Source>('/sources', data),
  update: (id: number, data: Partial<Source>) => api.patch<Source>(`/sources/${id}`, data),
  delete: (id: number) => api.delete(`/sources/${id}`),
  enable: (id: number) => api.post<Source>(`/sources/${id}/enable`),
  disable: (id: number) => api.post<Source>(`/sources/${id}/disable`),
  fetchAll: (trainingMode?: boolean) =>
    api.post('/sources/fetch-all', null, { params: { training_mode: trainingMode } }),
  fetchAllChannels: (id: number, trainingMode?: boolean) =>
    api.post(`/sources/${id}/fetch-all`, null, { params: { training_mode: trainingMode } }),
  // Add channel to source
  addChannel: (sourceId: number, data: ChannelCreate) =>
    api.post<Channel>(`/sources/${sourceId}/channels`, data)
}

export const channelsApi = {
  get: (id: number) => api.get<Channel>(`/channels/${id}`),
  update: (id: number, data: Partial<Channel>) => api.patch<Channel>(`/channels/${id}`, data),
  delete: (id: number) => api.delete(`/channels/${id}`),
  fetch: (id: number, trainingMode?: boolean) =>
    api.post(`/channels/${id}/fetch`, null, { params: { training_mode: trainingMode } }),
  enable: (id: number) => api.post<Channel>(`/channels/${id}/enable`),
  disable: (id: number) => api.post<Channel>(`/channels/${id}/disable`)
}

export const itemsApi = {
  list: (params?: {
    page?: number
    page_size?: number
    priority?: string
    source_id?: number
    channel_id?: number
    is_read?: boolean
    is_starred?: boolean
    is_archived?: boolean
    since?: string
    until?: string
    connector_type?: string
    assigned_ak?: string
    sort_by?: string
    sort_order?: string
    search?: string
    relevant_only?: boolean
  }) => api.get<PaginatedResponse<Item>>('/items', { params }),
  get: (id: number) => api.get<Item>(`/items/${id}`),
  update: (id: number, data: Partial<Item>) => api.patch<Item>(`/items/${id}`, data),
  markRead: (id: number) => api.post(`/items/${id}/read`),
  markUnread: (id: number) => api.patch(`/items/${id}`, { is_read: false }),
  bulkMarkRead: (ids: number[]) => api.post('/items/mark-all-read', { ids }),
  bulkMarkUnread: (ids: number[]) => api.post('/items/bulk-update', { ids, is_read: false }),
  archive: (id: number) => api.post<{ status: string; is_archived: boolean }>(`/items/${id}/archive`),
  getHistory: (id: number) => api.get<ItemEvent[]>(`/items/${id}/history`)
}

export const rulesApi = {
  list: () => api.get<Rule[]>('/rules'),
  get: (id: number) => api.get<Rule>(`/rules/${id}`),
  create: (data: Partial<Rule>) => api.post<Rule>('/rules', data),
  update: (id: number, data: Partial<Rule>) => api.put<Rule>(`/rules/${id}`, data),
  delete: (id: number) => api.delete(`/rules/${id}`),
  test: (id: number, content: string) => api.post(`/rules/${id}/test`, { content })
}

export const statsApi = {
  get: () => api.get<Stats>('/stats'),
  byPriority: () => api.get<Record<string, number>>('/stats/by-priority'),
  bySource: (sourceId?: number) =>
    api.get<SourceStats[]>('/stats/by-source', { params: sourceId ? { source_id: sourceId } : undefined }),
  byChannel: (params?: { source_id?: number; connector_type?: string }) =>
    api.get<ChannelStats[]>('/stats/by-channel', { params }),
  byConnector: () => api.get<{ connector_type: string; channel_count: number; item_count: number; unread_count: number }[]>('/stats/by-connector')
}

export const connectorsApi = {
  list: () => api.get('/connectors'),
  validate: (type: string, config: Record<string, unknown>) =>
    api.post(`/connectors/${type}/validate`, config)
}

export interface SendBriefingRequest {
  recipients: string[]
  min_priority: string
  hours_back: number
  include_read: boolean
}

export interface SendBriefingResponse {
  success: boolean
  message: string
  items_count: number
}

export interface PreviewBriefingResponse {
  subject: string
  text_body: string
  html_body: string
  items_count: number
  items_by_priority: Record<string, number>
}

export const emailApi = {
  sendBriefing: (data: SendBriefingRequest) =>
    api.post<SendBriefingResponse>('/email/send-briefing', data),
  previewBriefing: (data: Omit<SendBriefingRequest, 'recipients'>) =>
    api.post<PreviewBriefingResponse>('/email/preview-briefing', data),
  testEmail: (recipient: string) =>
    api.post<{ success: boolean; message: string }>(`/email/test?recipient=${encodeURIComponent(recipient)}`)
}

export interface OllamaModel {
  name: string
  is_current: boolean
}

export interface OllamaModelsResponse {
  available: boolean
  models: OllamaModel[]
  current_model: string
  base_url: string
}

export interface LLMSettingsResponse {
  ollama_available: boolean
  ollama_base_url: string
  ollama_model: string
  openrouter_configured: boolean
  openrouter_model: string
}

export const llmApi = {
  getModels: () => api.get<OllamaModelsResponse>('/llm/models'),
  getSettings: () => api.get<LLMSettingsResponse>('/llm/settings')
}

// System Stats Types
export interface WorkerStatus {
  running: boolean
  paused: boolean
  stats: Record<string, unknown>
}

export interface SchedulerStatus {
  running: boolean
  jobs: Array<{
    id: string
    name: string
    next_run: string | null
    trigger: string
  }>
}

export interface ProcessingQueueStats {
  total: number
  by_retry_priority: Record<string, number>
  awaiting_classifier: number
}

export interface ItemStats {
  total: number
  by_priority: Record<string, number>
  unread: number
  starred: number
}

export interface SystemStatsResponse {
  scheduler: SchedulerStatus
  llm_worker: WorkerStatus
  classifier_worker: WorkerStatus
  processing_queue: ProcessingQueueStats
  items: ItemStats
  timestamp: string
}

export interface WorkerControlResponse {
  status: string
  message: string
}

export interface HousekeepingConfig {
  retention_days_high: number
  retention_days_medium: number
  retention_days_low: number
  retention_days_none: number
  autopurge_enabled: boolean
  exclude_starred: boolean
}

export interface CleanupPreview {
  total: number
  by_priority: Record<string, number>
  oldest_item_date: string | null
}

export interface CleanupResult {
  deleted: number
  by_priority: Record<string, number>
}

export interface StorageStats {
  postgresql_size_bytes: number
  postgresql_size_human: string
  /** Total items in PostgreSQL database */
  postgresql_items: number
  /** Items marked as duplicates (similar_to_id set) */
  postgresql_duplicates: number
  /** Disk size of semantic search index (nomic embeddings) */
  search_index_size_bytes: number
  search_index_size_human: string
  /** Items indexed for semantic search */
  search_index_items: number
  /** Disk size of duplicate detection index (paraphrase embeddings) */
  duplicate_index_size_bytes: number
  duplicate_index_size_human: string
  /** Items indexed for duplicate detection */
  duplicate_index_items: number
  total_size_bytes: number
  total_size_human: string
}

// GPU1 Status Types
export interface GPU1Status {
  enabled: boolean
  available: boolean
  was_sleeping: boolean
  wake_time: string | null
  last_activity: number | null
  idle_time: number | null
  auto_shutdown: boolean
  idle_timeout: number
  pending_shutdown: boolean
  active_hours_start: number
  active_hours_end: number
  within_active_hours: boolean
  logged_in_users: string[]
  mac_address: string
  ssh_host: string
}

// Log Types
export interface LogEntry {
  timestamp: string
  level: string
  logger: string
  message: string
}

export interface LogsResponse {
  entries: LogEntry[]
  total: number
  page: number
  page_size: number
  total_pages: number
  source: string
}

export interface LogStats {
  total: number
  max_entries: number
  by_level: Record<string, number>
  by_logger: Record<string, number>
}

// Health Check Types
export interface HealthCheckResponse {
  status: string
  instance_type: string
  llm_enabled: boolean
  scheduler_running: boolean
  scheduler_jobs: Array<Record<string, unknown>>
  llm_available: boolean
  llm_provider: string | null
  proxy_count: number
  proxy_working: number
  database_ok: boolean
  database_info: {
    type: string
    host?: string
    database?: string
    path?: string
  }
  items_count: number
  sources_count: number
}

export const adminApi = {
  // Stats
  getStats: () => api.get<SystemStatsResponse>('/admin/stats'),

  // Health
  getHealth: () => api.get<HealthCheckResponse>('/admin/health'),

  // GPU1 Status
  getGpu1Status: () => api.get<GPU1Status>('/admin/gpu1/status'),

  // Logs
  getLogs: (params?: { page?: number; page_size?: number; level?: string; logger?: string; search?: string }) =>
    api.get<LogsResponse>('/admin/logs', { params }),
  getLogStats: () => api.get<LogStats>('/admin/logs/stats'),

  // Scheduler controls
  startScheduler: () => api.post<WorkerControlResponse>('/admin/scheduler/start'),
  stopScheduler: () => api.post<WorkerControlResponse>('/admin/scheduler/stop'),

  // LLM Worker controls
  startLlmWorker: () => api.post<WorkerControlResponse>('/admin/llm-worker/start'),
  stopLlmWorker: () => api.post<WorkerControlResponse>('/admin/llm-worker/stop'),
  pauseLlmWorker: () => api.post<WorkerControlResponse>('/admin/llm-worker/pause'),
  resumeLlmWorker: () => api.post<WorkerControlResponse>('/admin/llm-worker/resume'),

  // Classifier Worker controls
  startClassifierWorker: () => api.post<WorkerControlResponse>('/admin/classifier-worker/start'),
  stopClassifierWorker: () => api.post<WorkerControlResponse>('/admin/classifier-worker/stop'),
  pauseClassifierWorker: () => api.post<WorkerControlResponse>('/admin/classifier-worker/pause'),
  resumeClassifierWorker: () => api.post<WorkerControlResponse>('/admin/classifier-worker/resume'),

  // Housekeeping / Data Management
  getHousekeeping: () => api.get<HousekeepingConfig>('/admin/housekeeping'),
  updateHousekeeping: (config: HousekeepingConfig) =>
    api.put<HousekeepingConfig>('/admin/housekeeping', config),
  previewCleanup: () => api.post<CleanupPreview>('/admin/housekeeping/preview'),
  executeCleanup: () => api.post<CleanupResult>('/admin/housekeeping/cleanup'),
  getStorage: () => api.get<StorageStats>('/admin/storage'),
}

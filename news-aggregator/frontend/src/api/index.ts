import api from './client'
import type { Source, SourceCreate, Channel, ChannelCreate, Item, Rule, Stats, SourceStats, ChannelStats, PaginatedResponse } from '@/types'

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
  archive: (id: number) => api.post<{ status: string; is_archived: boolean }>(`/items/${id}/archive`)
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

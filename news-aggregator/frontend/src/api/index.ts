import api from './client'
import type { Source, Item, Rule, Stats, PaginatedResponse } from '@/types'

export const sourcesApi = {
  list: () => api.get<Source[]>('/sources'),
  get: (id: number) => api.get<Source>(`/sources/${id}`),
  create: (data: Partial<Source>) => api.post<Source>('/sources', data),
  update: (id: number, data: Partial<Source>) => api.put<Source>(`/sources/${id}`, data),
  delete: (id: number) => api.delete(`/sources/${id}`),
  fetch: (id: number) => api.post(`/sources/${id}/fetch`),
  fetchAll: () => api.post('/sources/fetch-all')
}

export const itemsApi = {
  list: (params?: {
    skip?: number
    limit?: number
    priority?: string
    source_id?: number
    is_read?: boolean
  }) => api.get<PaginatedResponse<Item>>('/items', { params }),
  get: (id: number) => api.get<Item>(`/items/${id}`),
  update: (id: number, data: Partial<Item>) => api.put<Item>(`/items/${id}`, data),
  markRead: (id: number) => api.post(`/items/${id}/mark-read`),
  markUnread: (id: number) => api.post(`/items/${id}/mark-unread`),
  bulkMarkRead: (ids: number[]) => api.post('/items/bulk-mark-read', { ids }),
  archive: (id: number) => api.post(`/items/${id}/archive`)
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
  priorities: () => api.get('/stats/priorities'),
  sources: () => api.get('/stats/sources')
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

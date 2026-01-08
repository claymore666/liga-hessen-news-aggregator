export type Priority = 'critical' | 'high' | 'medium' | 'low'
export type ConnectorType = 'rss' | 'html' | 'bluesky' | 'twitter' | 'pdf' | 'mastodon' | 'x_scraper' | 'telegram' | 'instagram' | 'instagram_scraper' | 'google_alerts'
export type RuleType = 'keyword' | 'regex' | 'semantic'

export interface Source {
  id: number
  name: string
  connector_type: ConnectorType
  config: Record<string, unknown>
  enabled: boolean
  fetch_interval_minutes: number
  is_stakeholder: boolean
  last_fetch_at: string | null
  last_error: string | null
  created_at: string
  updated_at: string
}

export interface LLMAnalysis {
  relevance_score?: number
  priority_suggestion?: string
  assigned_ak?: string
  tags?: string[]
  reasoning?: string
}

export interface ItemMetadata {
  llm_analysis?: LLMAnalysis
  [key: string]: unknown
}

export interface Item {
  id: number
  source_id: number
  source?: Source
  external_id: string
  title: string
  content: string
  summary: string | null
  url: string
  author: string | null
  published_at: string | null
  priority: Priority
  priority_score: number
  is_read: boolean
  is_archived: boolean
  assigned_ak: string | null
  tags: string[]
  metadata?: ItemMetadata
  created_at: string
  updated_at: string
}

export interface Rule {
  id: number
  name: string
  description: string | null
  rule_type: RuleType
  pattern: string
  priority_boost: number
  target_priority: Priority | null
  enabled: boolean
  order: number
  created_at: string
  updated_at: string
}

export interface Stats {
  total_items: number
  unread_items: number
  items_by_priority: Record<Priority, number>
  items_by_source: Record<number, number>
  sources_count: number
  rules_count: number
  last_fetch_at: string | null
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface ConnectorInfo {
  type: ConnectorType
  name: string
  description: string
  config_schema: Record<string, unknown>
}

export interface ValidationResult {
  valid: boolean
  message: string
  sample_items?: number
}

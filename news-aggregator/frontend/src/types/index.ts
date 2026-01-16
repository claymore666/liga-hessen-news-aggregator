export type Priority = 'high' | 'medium' | 'low' | 'none'
export type ConnectorType = 'rss' | 'html' | 'bluesky' | 'twitter' | 'pdf' | 'mastodon' | 'x_scraper' | 'telegram' | 'instagram' | 'instagram_scraper' | 'google_alerts'
export type RuleType = 'keyword' | 'regex' | 'semantic'

// Channel represents a single feed/connector within a source organization
export interface Channel {
  id: number
  source_id: number
  name: string | null
  connector_type: ConnectorType
  config: Record<string, unknown>
  source_identifier: string | null
  enabled: boolean
  fetch_interval_minutes: number
  last_fetch_at: string | null
  last_error: string | null
  created_at: string
  updated_at: string
}

// Source represents an organization with multiple channels
export interface Source {
  id: number
  name: string
  description: string | null
  is_stakeholder: boolean
  enabled: boolean
  channels: Channel[]
  channel_count: number
  enabled_channel_count: number
  created_at: string
  updated_at: string
}

// For creating channels
export interface ChannelCreate {
  name?: string | null
  connector_type: ConnectorType
  config: Record<string, unknown>
  enabled?: boolean
  fetch_interval_minutes?: number
}

// For creating sources with initial channels
export interface SourceCreate {
  name: string
  description?: string | null
  is_stakeholder?: boolean
  enabled?: boolean
  channels?: ChannelCreate[]
}

export interface LLMAnalysis {
  relevance_score?: number
  priority_suggestion?: string
  assigned_aks?: string[]
  assigned_ak?: string  // Deprecated, use assigned_aks
  tags?: string[]
  reasoning?: string
}

export interface ItemMetadata {
  llm_analysis?: LLMAnalysis
  [key: string]: unknown
}

// Brief channel info for items (without full channel details)
export interface ChannelBrief {
  id: number
  source_id: number
  name: string | null
  connector_type: ConnectorType
}

// Brief source info for items (without channels list)
export interface SourceBrief {
  id: number
  name: string
  is_stakeholder: boolean
}

// Brief duplicate info for collapsible grouping
export interface DuplicateBrief {
  id: number
  title: string
  url: string
  priority: Priority
  source?: SourceBrief
  published_at: string | null
}

export interface Item {
  id: number
  channel_id: number
  channel?: ChannelBrief
  source?: SourceBrief
  // Legacy compatibility - derived from channel
  source_id?: number
  external_id: string
  title: string
  content: string
  summary: string | null
  detailed_analysis: string | null
  url: string
  author: string | null
  published_at: string | null
  priority: Priority
  priority_score: number
  is_read: boolean
  is_starred: boolean
  is_archived: boolean
  assigned_aks: string[]
  assigned_ak: string | null  // Deprecated, use assigned_aks
  is_manually_reviewed: boolean
  reviewed_at: string | null
  tags: string[]
  metadata?: ItemMetadata
  created_at: string
  updated_at: string
  // Duplicate grouping
  similar_to_id: number | null
  duplicates: DuplicateBrief[]
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
  relevant_items: number
  unread_items: number
  starred_items: number
  high_items: number
  medium_items: number
  items_by_priority: Record<Priority, number>
  sources_count: number
  channels_count: number
  enabled_sources: number
  enabled_channels: number
  rules_count: number
  items_today: number
  items_this_week: number
  last_fetch_at: string | null
}

// Stats grouped by source (organization)
export interface SourceStats {
  source_id: number
  name: string
  is_stakeholder: boolean
  enabled: boolean
  channel_count: number
  item_count: number
  unread_count: number
}

// Stats grouped by channel
export interface ChannelStats {
  channel_id: number
  source_id: number
  source_name: string
  connector_type: ConnectorType
  name: string | null
  enabled: boolean
  item_count: number
  unread_count: number
  last_fetch_at: string | null
  last_error: string | null
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

export interface ItemEvent {
  id: number
  event_type: string
  timestamp: string
  ip_address: string | null
  data: Record<string, unknown> | null
}

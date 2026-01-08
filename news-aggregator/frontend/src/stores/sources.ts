import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { sourcesApi, channelsApi } from '@/api'
import type { Source, SourceCreate, Channel, ChannelCreate, ConnectorType } from '@/types'

export const useSourcesStore = defineStore('sources', () => {
  const sources = ref<Source[]>([])
  const currentSource = ref<Source | null>(null)
  const loading = ref(false)
  const fetchingSourceId = ref<number | null>(null)
  const fetchingChannelId = ref<number | null>(null)
  const error = ref<string | null>(null)

  const enabledSources = computed(() => sources.value.filter((s) => s.enabled))

  // Group sources by whether they have errors
  const sourcesWithErrors = computed(() =>
    sources.value.filter((s) => s.channels.some((c) => c.last_error))
  )

  // Get all channels across all sources
  const allChannels = computed(() =>
    sources.value.flatMap((s) => s.channels.map((c) => ({ ...c, source: s })))
  )

  // Group channels by connector type
  const channelsByType = computed(() => {
    const grouped: Record<string, (Channel & { source: Source })[]> = {}
    allChannels.value.forEach((channel) => {
      if (!grouped[channel.connector_type]) {
        grouped[channel.connector_type] = []
      }
      grouped[channel.connector_type].push(channel)
    })
    return grouped
  })

  // Get total channel count
  const totalChannels = computed(() =>
    sources.value.reduce((sum, s) => sum + s.channels.length, 0)
  )

  // Get enabled channel count
  const enabledChannels = computed(() =>
    sources.value.reduce(
      (sum, s) => sum + (s.enabled ? s.channels.filter((c) => c.enabled).length : 0),
      0
    )
  )

  async function fetchSources(params?: { enabled?: boolean; has_errors?: boolean }) {
    loading.value = true
    error.value = null
    try {
      const response = await sourcesApi.list(params)
      sources.value = response.data
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch sources'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function fetchSource(id: number) {
    loading.value = true
    error.value = null
    try {
      const response = await sourcesApi.get(id)
      currentSource.value = response.data
      // Update in list as well
      const index = sources.value.findIndex((s) => s.id === id)
      if (index !== -1) sources.value[index] = response.data
      return response.data
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch source'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function createSource(data: SourceCreate) {
    loading.value = true
    error.value = null
    try {
      const response = await sourcesApi.create(data)
      sources.value.push(response.data)
      return response.data
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to create source'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function updateSource(id: number, data: Partial<Source>) {
    loading.value = true
    error.value = null
    try {
      const response = await sourcesApi.update(id, data)
      const index = sources.value.findIndex((s) => s.id === id)
      if (index !== -1) sources.value[index] = response.data
      if (currentSource.value?.id === id) currentSource.value = response.data
      return response.data
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to update source'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function deleteSource(id: number) {
    loading.value = true
    error.value = null
    try {
      await sourcesApi.delete(id)
      sources.value = sources.value.filter((s) => s.id !== id)
      if (currentSource.value?.id === id) currentSource.value = null
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to delete source'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function enableSource(id: number) {
    error.value = null
    try {
      const response = await sourcesApi.enable(id)
      const index = sources.value.findIndex((s) => s.id === id)
      if (index !== -1) sources.value[index] = response.data
      if (currentSource.value?.id === id) currentSource.value = response.data
      return response.data
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to enable source'
      throw e
    }
  }

  async function disableSource(id: number) {
    error.value = null
    try {
      const response = await sourcesApi.disable(id)
      const index = sources.value.findIndex((s) => s.id === id)
      if (index !== -1) sources.value[index] = response.data
      if (currentSource.value?.id === id) currentSource.value = response.data
      return response.data
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to disable source'
      throw e
    }
  }

  // === Channel Management ===

  async function addChannel(sourceId: number, data: ChannelCreate) {
    error.value = null
    try {
      const response = await sourcesApi.addChannel(sourceId, data)
      // Refresh the source to get updated channel list
      await fetchSource(sourceId)
      return response.data
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to add channel'
      throw e
    }
  }

  async function updateChannel(channelId: number, data: Partial<Channel>) {
    error.value = null
    try {
      const response = await channelsApi.update(channelId, data)
      // Update channel in sources list
      for (const source of sources.value) {
        const channelIndex = source.channels.findIndex((c) => c.id === channelId)
        if (channelIndex !== -1) {
          source.channels[channelIndex] = response.data
          break
        }
      }
      return response.data
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to update channel'
      throw e
    }
  }

  async function deleteChannel(channelId: number) {
    error.value = null
    try {
      await channelsApi.delete(channelId)
      // Remove channel from sources list
      for (const source of sources.value) {
        const channelIndex = source.channels.findIndex((c) => c.id === channelId)
        if (channelIndex !== -1) {
          source.channels.splice(channelIndex, 1)
          source.channel_count = source.channels.length
          source.enabled_channel_count = source.channels.filter((c) => c.enabled).length
          break
        }
      }
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to delete channel'
      throw e
    }
  }

  async function enableChannel(channelId: number) {
    error.value = null
    try {
      const response = await channelsApi.enable(channelId)
      // Update channel in sources list
      for (const source of sources.value) {
        const channelIndex = source.channels.findIndex((c) => c.id === channelId)
        if (channelIndex !== -1) {
          source.channels[channelIndex] = response.data
          source.enabled_channel_count = source.channels.filter((c) => c.enabled).length
          break
        }
      }
      return response.data
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to enable channel'
      throw e
    }
  }

  async function disableChannel(channelId: number) {
    error.value = null
    try {
      const response = await channelsApi.disable(channelId)
      // Update channel in sources list
      for (const source of sources.value) {
        const channelIndex = source.channels.findIndex((c) => c.id === channelId)
        if (channelIndex !== -1) {
          source.channels[channelIndex] = response.data
          source.enabled_channel_count = source.channels.filter((c) => c.enabled).length
          break
        }
      }
      return response.data
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to disable channel'
      throw e
    }
  }

  async function fetchChannel(channelId: number, trainingMode = false) {
    fetchingChannelId.value = channelId
    error.value = null
    try {
      const result = await channelsApi.fetch(channelId, trainingMode)
      // Refresh the source that contains this channel
      for (const source of sources.value) {
        const channel = source.channels.find((c) => c.id === channelId)
        if (channel) {
          await fetchSource(source.id)
          break
        }
      }
      return result.data
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch channel'
      throw e
    } finally {
      fetchingChannelId.value = null
    }
  }

  // Fetch all channels for a specific source
  async function fetchAllSourceChannels(sourceId: number, trainingMode = false) {
    fetchingSourceId.value = sourceId
    error.value = null
    try {
      const result = await sourcesApi.fetchAllChannels(sourceId, trainingMode)
      await fetchSource(sourceId)
      return result.data
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch source channels'
      throw e
    } finally {
      fetchingSourceId.value = null
    }
  }

  // Fetch all channels across all sources
  async function fetchAllChannels(trainingMode = false) {
    fetchingSourceId.value = -1 // -1 indicates fetching all
    error.value = null
    try {
      const result = await sourcesApi.fetchAll(trainingMode)
      await fetchSources()
      return result.data
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch all sources'
      throw e
    } finally {
      fetchingSourceId.value = null
    }
  }

  // Helper to get channel's display name
  function getChannelDisplayName(channel: Channel, source?: Source): string {
    if (channel.name) {
      return channel.name
    }
    // Fall back to connector type
    const typeLabels: Record<ConnectorType, string> = {
      rss: 'RSS Feed',
      html: 'HTML Scraper',
      bluesky: 'Bluesky',
      twitter: 'Twitter/Nitter',
      pdf: 'PDF',
      mastodon: 'Mastodon',
      x_scraper: 'X.com',
      telegram: 'Telegram',
      instagram: 'Instagram',
      instagram_scraper: 'Instagram',
      google_alerts: 'Google Alerts'
    }
    return typeLabels[channel.connector_type] || channel.connector_type
  }

  return {
    sources,
    currentSource,
    loading,
    fetchingSourceId,
    fetchingChannelId,
    error,
    // Computed
    enabledSources,
    sourcesWithErrors,
    allChannels,
    channelsByType,
    totalChannels,
    enabledChannels,
    // Source actions
    fetchSources,
    fetchSource,
    createSource,
    updateSource,
    deleteSource,
    enableSource,
    disableSource,
    // Channel actions
    addChannel,
    updateChannel,
    deleteChannel,
    enableChannel,
    disableChannel,
    fetchChannel,
    fetchAllSourceChannels,
    fetchAllChannels,
    // Helpers
    getChannelDisplayName
  }
})

import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { sourcesApi } from '@/api'
import type { Source } from '@/types'

export const useSourcesStore = defineStore('sources', () => {
  const sources = ref<Source[]>([])
  const currentSource = ref<Source | null>(null)
  const loading = ref(false)
  const fetching = ref<number | null>(null)
  const error = ref<string | null>(null)

  const enabledSources = computed(() => sources.value.filter((s) => s.enabled))

  const sourcesByType = computed(() => {
    const grouped: Record<string, Source[]> = {}
    sources.value.forEach((source) => {
      if (!grouped[source.connector_type]) {
        grouped[source.connector_type] = []
      }
      grouped[source.connector_type].push(source)
    })
    return grouped
  })

  async function fetchSources() {
    loading.value = true
    error.value = null
    try {
      const response = await sourcesApi.list()
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
      return response.data
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch source'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function createSource(data: Partial<Source>) {
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

  async function triggerFetch(id: number) {
    fetching.value = id
    error.value = null
    try {
      await sourcesApi.fetch(id)
      await fetchSource(id)
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch source'
      throw e
    } finally {
      fetching.value = null
    }
  }

  async function triggerFetchAll() {
    fetching.value = -1
    error.value = null
    try {
      await sourcesApi.fetchAll()
      await fetchSources()
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch all sources'
      throw e
    } finally {
      fetching.value = null
    }
  }

  return {
    sources,
    currentSource,
    loading,
    fetching,
    error,
    enabledSources,
    sourcesByType,
    fetchSources,
    fetchSource,
    createSource,
    updateSource,
    deleteSource,
    triggerFetch,
    triggerFetchAll
  }
})

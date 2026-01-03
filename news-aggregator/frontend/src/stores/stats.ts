import { ref } from 'vue'
import { defineStore } from 'pinia'
import { statsApi } from '@/api'
import type { Stats } from '@/types'

export const useStatsStore = defineStore('stats', () => {
  const stats = ref<Stats | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchStats() {
    loading.value = true
    error.value = null
    try {
      const response = await statsApi.get()
      stats.value = response.data
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch stats'
      throw e
    } finally {
      loading.value = false
    }
  }

  return {
    stats,
    loading,
    error,
    fetchStats
  }
})

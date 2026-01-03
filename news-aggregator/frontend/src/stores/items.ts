import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { itemsApi } from '@/api'
import type { Item, Priority } from '@/types'

export const useItemsStore = defineStore('items', () => {
  const items = ref<Item[]>([])
  const currentItem = ref<Item | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const total = ref(0)
  const filters = ref({
    priority: null as Priority | null,
    source_id: null as number | null,
    is_read: null as boolean | null
  })

  const unreadCount = computed(() => items.value.filter((i) => !i.is_read).length)

  const criticalItems = computed(() =>
    items.value.filter((i) => i.priority === 'critical' && !i.is_read)
  )

  const highPriorityItems = computed(() =>
    items.value.filter((i) => ['critical', 'high'].includes(i.priority) && !i.is_read)
  )

  async function fetchItems(params?: { skip?: number; limit?: number }) {
    loading.value = true
    error.value = null
    try {
      const response = await itemsApi.list({
        ...params,
        priority: filters.value.priority || undefined,
        source_id: filters.value.source_id || undefined,
        is_read: filters.value.is_read ?? undefined
      })
      items.value = response.data.items
      total.value = response.data.total
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch items'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function fetchItem(id: number) {
    loading.value = true
    error.value = null
    try {
      const response = await itemsApi.get(id)
      currentItem.value = response.data
      return response.data
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch item'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function markAsRead(id: number) {
    try {
      await itemsApi.markRead(id)
      const item = items.value.find((i) => i.id === id)
      if (item) item.is_read = true
      if (currentItem.value?.id === id) currentItem.value.is_read = true
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to mark as read'
      throw e
    }
  }

  async function markAsUnread(id: number) {
    try {
      await itemsApi.markUnread(id)
      const item = items.value.find((i) => i.id === id)
      if (item) item.is_read = false
      if (currentItem.value?.id === id) currentItem.value.is_read = false
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to mark as unread'
      throw e
    }
  }

  async function bulkMarkAsRead(ids: number[]) {
    try {
      await itemsApi.bulkMarkRead(ids)
      items.value.forEach((item) => {
        if (ids.includes(item.id)) item.is_read = true
      })
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to mark items as read'
      throw e
    }
  }

  async function archiveItem(id: number) {
    try {
      await itemsApi.archive(id)
      items.value = items.value.filter((i) => i.id !== id)
      if (currentItem.value?.id === id) currentItem.value = null
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to archive item'
      throw e
    }
  }

  function setFilter(key: keyof typeof filters.value, value: unknown) {
    filters.value[key] = value as never
  }

  function clearFilters() {
    filters.value = {
      priority: null,
      source_id: null,
      is_read: null
    }
  }

  return {
    items,
    currentItem,
    loading,
    error,
    total,
    filters,
    unreadCount,
    criticalItems,
    highPriorityItems,
    fetchItems,
    fetchItem,
    markAsRead,
    markAsUnread,
    bulkMarkAsRead,
    archiveItem,
    setFilter,
    clearFilters
  }
})

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
    is_read: null as boolean | null,
    is_archived: null as boolean | null,
    connector_type: null as string | null,
    assigned_ak: null as string | null,
    sort_by: 'date' as string,
    sort_order: 'desc' as string,
    search: null as string | null,
    since: null as string | null
  })

  const unreadCount = computed(() => items.value.filter((i) => !i.is_read).length)

  const highItems = computed(() =>
    items.value.filter((i) => i.priority === 'high' && !i.is_read)
  )

  const highPriorityItems = computed(() =>
    items.value.filter((i) => ['high', 'medium'].includes(i.priority) && !i.is_read)
  )

  async function fetchItems(params?: { page?: number; page_size?: number }) {
    loading.value = true
    error.value = null
    try {
      const response = await itemsApi.list({
        ...params,
        priority: filters.value.priority || undefined,
        source_id: filters.value.source_id || undefined,
        is_read: filters.value.is_read ?? undefined,
        is_archived: filters.value.is_archived ?? undefined,
        connector_type: filters.value.connector_type || undefined,
        assigned_ak: filters.value.assigned_ak || undefined,
        sort_by: filters.value.sort_by,
        sort_order: filters.value.sort_order,
        search: filters.value.search || undefined,
        since: filters.value.since || undefined,
        // Show all items including 'none' priority when no filter is set
        relevant_only: false
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
      const response = await itemsApi.archive(id)
      const newArchivedState = response.data.is_archived
      // Update in items list
      const item = items.value.find((i) => i.id === id)
      if (item) item.is_archived = newArchivedState
      // Update currentItem
      if (currentItem.value?.id === id) {
        currentItem.value.is_archived = newArchivedState
      }
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to toggle archive'
      throw e
    }
  }

  async function updateItem(id: number, data: Partial<Item>) {
    // Optimistic update
    const item = items.value.find((i) => i.id === id)
    const backup = item ? { ...item } : null
    if (item) Object.assign(item, data)
    if (currentItem.value?.id === id) {
      Object.assign(currentItem.value, data)
    }

    try {
      const response = await itemsApi.update(id, data)
      // Update with server response
      if (item) Object.assign(item, response.data)
      if (currentItem.value?.id === id) {
        currentItem.value = response.data
      }
    } catch (e) {
      // Rollback on error
      if (item && backup) Object.assign(item, backup)
      if (currentItem.value?.id === id && backup) {
        Object.assign(currentItem.value, backup)
      }
      error.value = e instanceof Error ? e.message : 'Failed to update item'
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
      is_read: null,
      is_archived: null,
      connector_type: null,
      assigned_ak: null,
      sort_by: 'date',
      sort_order: 'desc',
      search: null,
      since: null
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
    highItems,
    highPriorityItems,
    fetchItems,
    fetchItem,
    markAsRead,
    markAsUnread,
    bulkMarkAsRead,
    archiveItem,
    updateItem,
    setFilter,
    clearFilters
  }
})

<script setup lang="ts">
import { onMounted, ref, watch, computed } from 'vue'
import { useRoute } from 'vue-router'
import { useItemsStore, useSourcesStore } from '@/stores'
import { ArrowPathIcon } from '@heroicons/vue/24/outline'
import FilterBar from '@/components/nachrichten/FilterBar.vue'
import MessageList from '@/components/nachrichten/MessageList.vue'
import MessageDetail from '@/components/nachrichten/MessageDetail.vue'
import FeedbackPanel from '@/components/nachrichten/FeedbackPanel.vue'
import type { Priority } from '@/types'
import { useKeyboardShortcuts } from '@/composables/useKeyboardShortcuts'

const route = useRoute()
const itemsStore = useItemsStore()
const sourcesStore = useSourcesStore()

const page = ref(1)
const pageSize = 50
const selectedItemId = ref<number | null>(null)
const selectedIds = ref<number[]>([])
const focusedIndex = ref(-1)

const selectedItem = computed(() => itemsStore.currentItem)

const focusedItem = computed(() =>
  focusedIndex.value >= 0 ? itemsStore.items[focusedIndex.value] : null
)

// Keyboard shortcuts
useKeyboardShortcuts([
  {
    key: 'j',
    description: 'Nächster Eintrag',
    action: () => {
      if (focusedIndex.value < itemsStore.items.length - 1) {
        focusedIndex.value++
        if (focusedItem.value) {
          selectItem(focusedItem.value.id)
        }
      }
    }
  },
  {
    key: 'k',
    description: 'Vorheriger Eintrag',
    action: () => {
      if (focusedIndex.value > 0) {
        focusedIndex.value--
        if (focusedItem.value) {
          selectItem(focusedItem.value.id)
        }
      } else if (focusedIndex.value === -1 && itemsStore.items.length > 0) {
        focusedIndex.value = 0
        selectItem(itemsStore.items[0].id)
      }
    }
  },
  {
    key: '1',
    description: 'Priorität: Kritisch',
    action: () => {
      if (selectedItem.value) {
        handlePriorityChange('critical')
      }
    }
  },
  {
    key: '2',
    description: 'Priorität: Hoch',
    action: () => {
      if (selectedItem.value) {
        handlePriorityChange('high')
      }
    }
  },
  {
    key: '3',
    description: 'Priorität: Mittel',
    action: () => {
      if (selectedItem.value) {
        handlePriorityChange('medium')
      }
    }
  },
  {
    key: '4',
    description: 'Priorität: Niedrig',
    action: () => {
      if (selectedItem.value) {
        handlePriorityChange('low')
      }
    }
  },
  {
    key: 'm',
    description: 'Als gelesen/ungelesen',
    action: () => {
      if (selectedItem.value) {
        handleToggleRead()
      }
    }
  },
  {
    key: 'x',
    description: 'Auswählen/Abwählen',
    action: () => {
      if (focusedItem.value) {
        toggleCheck(focusedItem.value.id)
      }
    }
  },
  {
    key: 'r',
    description: 'Relevanz umschalten',
    action: () => {
      if (selectedItem.value) {
        const isRelevant = selectedItem.value.priority !== 'low'
        handlePriorityChange(isRelevant ? 'low' : 'medium')
      }
    }
  }
])

const loadItems = async () => {
  await itemsStore.fetchItems({
    page: page.value,
    page_size: pageSize
  })
  // Auto-select first item if none selected
  if (!selectedItemId.value && itemsStore.items.length > 0) {
    selectItem(itemsStore.items[0].id)
    focusedIndex.value = 0
  }
}

const selectItem = async (id: number) => {
  selectedItemId.value = id
  await itemsStore.fetchItem(id)
  // Mark as read when selected
  if (selectedItem.value && !selectedItem.value.is_read) {
    await itemsStore.markAsRead(id)
  }
}

const toggleCheck = (id: number) => {
  if (selectedIds.value.includes(id)) {
    selectedIds.value = selectedIds.value.filter(i => i !== id)
  } else {
    selectedIds.value.push(id)
  }
}

const toggleSelectAll = () => {
  if (selectedIds.value.length === itemsStore.items.length) {
    selectedIds.value = []
  } else {
    selectedIds.value = itemsStore.items.map(i => i.id)
  }
}

const clearSelection = () => {
  selectedIds.value = []
}

const bulkMarkRead = async () => {
  if (selectedIds.value.length > 0) {
    await itemsStore.bulkMarkAsRead(selectedIds.value)
    selectedIds.value = []
  }
}

const handleSearch = () => {
  page.value = 1
  loadItems()
}

const handlePriorityChange = async (priority: Priority) => {
  if (selectedItem.value) {
    await itemsStore.updateItem(selectedItem.value.id, { priority })
  }
}

const handleAkChange = async (ak: string | null) => {
  if (selectedItem.value) {
    await itemsStore.updateItem(selectedItem.value.id, { assigned_ak: ak })
  }
}

const handleToggleRead = async () => {
  if (selectedItem.value) {
    if (selectedItem.value.is_read) {
      await itemsStore.markAsUnread(selectedItem.value.id)
    } else {
      await itemsStore.markAsRead(selectedItem.value.id)
    }
  }
}

const handleArchive = async () => {
  if (selectedItem.value) {
    await itemsStore.archiveItem(selectedItem.value.id)
  }
}

// Watch for filter changes
watch(
  () => [
    itemsStore.filters.priority,
    itemsStore.filters.source_id,
    itemsStore.filters.connector_type,
    itemsStore.filters.assigned_ak,
    itemsStore.filters.is_read,
    itemsStore.filters.sort_by
  ],
  () => {
    page.value = 1
    loadItems()
  }
)

// Handle deep-link to specific item
watch(
  () => route.params.id,
  async (newId) => {
    if (newId) {
      const id = parseInt(newId as string)
      if (id) {
        await selectItem(id)
        const index = itemsStore.items.findIndex(i => i.id === id)
        if (index >= 0) focusedIndex.value = index
      }
    }
  },
  { immediate: true }
)

onMounted(async () => {
  await Promise.all([loadItems(), sourcesStore.fetchSources()])
})
</script>

<template>
  <div class="flex flex-col h-[calc(100vh-4rem)]">
    <!-- Header -->
    <div class="flex items-center justify-between mb-3">
      <h1 class="text-xl font-bold text-gray-900">Nachrichten</h1>
      <button
        type="button"
        class="btn btn-primary text-sm"
        :disabled="itemsStore.loading"
        @click="loadItems"
      >
        <ArrowPathIcon
          class="mr-1.5 h-4 w-4"
          :class="{ 'animate-spin': itemsStore.loading }"
        />
        Aktualisieren
      </button>
    </div>

    <!-- Two-column layout -->
    <div class="flex-1 grid grid-cols-1 lg:grid-cols-[400px_1fr] gap-4 min-h-0">
      <!-- Left Column: List -->
      <div class="flex flex-col min-h-0 rounded-lg border border-blue-300 overflow-hidden">
        <!-- Filters -->
        <FilterBar
          :selected-count="selectedIds.length"
          @search="handleSearch"
          @clear-selection="clearSelection"
          @bulk-mark-read="bulkMarkRead"
          @select-all="toggleSelectAll"
        />

        <!-- Loading -->
        <div v-if="itemsStore.loading && itemsStore.items.length === 0" class="flex items-center justify-center py-6 bg-blue-100 flex-1">
          <ArrowPathIcon class="h-6 w-6 animate-spin text-blue-500" />
        </div>

        <!-- Message List -->
        <MessageList
          v-else
          :items="itemsStore.items"
          :selected-id="selectedItemId"
          :selected-ids="selectedIds"
          :focused-index="focusedIndex"
          @select="selectItem"
          @toggle-check="toggleCheck"
          @focus="focusedIndex = $event"
        />

        <!-- Pagination -->
        <div v-if="itemsStore.total > pageSize" class="flex items-center justify-between border-t border-blue-300 bg-blue-100 px-3 py-2 flex-shrink-0">
          <p class="text-xs text-black">
            {{ (page - 1) * pageSize + 1 }}-{{ Math.min(page * pageSize, itemsStore.total) }} von {{ itemsStore.total }}
          </p>
          <div class="flex gap-1">
            <button
              type="button"
              class="btn btn-secondary text-xs py-1 px-2"
              :disabled="page === 1"
              @click="page--; loadItems()"
            >
              Zurück
            </button>
            <button
              type="button"
              class="btn btn-secondary text-xs py-1 px-2"
              :disabled="page * pageSize >= itemsStore.total"
              @click="page++; loadItems()"
            >
              Weiter
            </button>
          </div>
        </div>
      </div>

      <!-- Right Column: Detail -->
      <div class="flex flex-col min-h-0 rounded-lg border border-gray-200 bg-white overflow-hidden">
        <template v-if="selectedItem">
          <!-- Feedback Panel (sticky) -->
          <div class="flex-shrink-0 p-3 border-b border-gray-200 bg-gray-50">
            <FeedbackPanel
              :item="selectedItem"
              @update:priority="handlePriorityChange"
              @update:ak="handleAkChange"
              @toggle:read="handleToggleRead"
              @archive="handleArchive"
            />
          </div>

          <!-- Detail Content -->
          <div class="flex-1 overflow-y-auto p-4">
            <MessageDetail :item="selectedItem" />
          </div>
        </template>

        <!-- Empty State -->
        <div v-else class="flex-1 flex items-center justify-center text-gray-500">
          <p>Wählen Sie eine Nachricht aus der Liste</p>
        </div>
      </div>
    </div>
  </div>
</template>

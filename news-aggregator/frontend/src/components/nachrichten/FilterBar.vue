<script setup lang="ts">
import { ref, computed } from 'vue'
import { useItemsStore, useSourcesStore } from '@/stores'
import {
  MagnifyingGlassIcon,
  ChevronUpDownIcon,
  XMarkIcon,
  CheckIcon
} from '@heroicons/vue/24/outline'

const props = defineProps<{
  selectedCount: number
}>()

const emit = defineEmits<{
  (e: 'search'): void
  (e: 'clear-selection'): void
  (e: 'bulk-mark-read'): void
  (e: 'select-all'): void
}>()

const itemsStore = useItemsStore()
const sourcesStore = useSourcesStore()

const searchQuery = ref(itemsStore.filters.search ?? '')

const priorities = [
  { value: '', label: 'Alle Prioritäten' },
  { value: 'high', label: 'Hoch' },
  { value: 'medium', label: 'Mittel' },
  { value: 'low', label: 'Niedrig' },
  { value: 'none', label: 'Keine' }
]

const connectorTypes = [
  { value: '', label: 'Alle Typen' },
  { value: 'rss', label: 'RSS' },
  { value: 'x_scraper', label: 'X/Twitter' },
  { value: 'mastodon', label: 'Mastodon' },
  { value: 'bluesky', label: 'Bluesky' },
  { value: 'telegram', label: 'Telegram' },
  { value: 'html', label: 'HTML' },
  { value: 'pdf', label: 'PDF' },
  { value: 'instagram_scraper', label: 'Instagram' }
]

const arbeitskreise = [
  { value: '', label: 'Alle AKs' },
  { value: 'AK1', label: 'AK1' },
  { value: 'AK2', label: 'AK2' },
  { value: 'AK3', label: 'AK3' },
  { value: 'AK4', label: 'AK4' },
  { value: 'AK5', label: 'AK5' },
  { value: 'QAG', label: 'QAG' }
]

const sortOptions = [
  { value: 'date', label: 'Datum' },
  { value: 'priority', label: 'Priorität' },
  { value: 'source', label: 'Quelle' }
]

const hasActiveFilters = computed(() => {
  return (
    itemsStore.filters.priority ||
    itemsStore.filters.source_id ||
    itemsStore.filters.connector_type ||
    itemsStore.filters.assigned_ak ||
    itemsStore.filters.search ||
    itemsStore.filters.is_read !== null ||
    itemsStore.filters.is_archived !== null
  )
})

const applySearch = () => {
  itemsStore.setFilter('search', searchQuery.value || null)
  emit('search')
}

const clearAllFilters = () => {
  searchQuery.value = ''
  itemsStore.clearFilters()
  emit('search')
}

const toggleSortOrder = () => {
  const newOrder = itemsStore.filters.sort_order === 'desc' ? 'asc' : 'desc'
  itemsStore.setFilter('sort_order', newOrder)
  emit('search')
}

const handleFilterChange = (key: string, value: string | number | boolean | null) => {
  itemsStore.setFilter(key as keyof typeof itemsStore.filters, value)
  emit('search')
}
</script>

<template>
  <div class="space-y-2">
    <!-- Bulk Actions -->
    <div v-if="selectedCount > 0" class="rounded-lg border border-blue-300 bg-blue-100 px-3 py-2 flex items-center gap-3">
      <span class="text-sm font-medium text-black">
        {{ selectedCount }} ausgewählt
      </span>
      <button
        type="button"
        class="btn btn-secondary text-xs py-1"
        @click="emit('bulk-mark-read')"
      >
        <CheckIcon class="mr-1 h-3.5 w-3.5" />
        Als gelesen
      </button>
      <button
        type="button"
        class="text-gray-500 hover:text-gray-700"
        @click="emit('clear-selection')"
      >
        <XMarkIcon class="h-4 w-4" />
      </button>
    </div>

    <!-- Filter Bar -->
    <div class="flex flex-wrap items-center gap-2 px-3 py-2 bg-blue-400 rounded-lg">
      <!-- Search -->
      <div class="relative">
        <input
          v-model="searchQuery"
          type="text"
          placeholder="Suchen..."
          class="input pl-8 text-xs py-1.5 w-32"
          @keyup.enter="applySearch"
        />
        <MagnifyingGlassIcon class="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-gray-400" />
      </div>

      <!-- Connector Type -->
      <select
        class="input text-xs py-1.5 w-auto"
        :value="itemsStore.filters.connector_type ?? ''"
        @change="handleFilterChange('connector_type', ($event.target as HTMLSelectElement).value || null)"
      >
        <option v-for="t in connectorTypes" :key="t.value" :value="t.value">
          {{ t.label }}
        </option>
      </select>

      <!-- Source -->
      <select
        class="input text-xs py-1.5 w-auto max-w-32"
        :value="itemsStore.filters.source_id ?? ''"
        @change="handleFilterChange('source_id', parseInt(($event.target as HTMLSelectElement).value) || null)"
      >
        <option value="">Alle Quellen</option>
        <option v-for="s in sourcesStore.sources" :key="s.id" :value="s.id">
          {{ s.name }}
        </option>
      </select>

      <!-- Priority -->
      <select
        class="input text-xs py-1.5 w-auto"
        :value="itemsStore.filters.priority ?? ''"
        @change="handleFilterChange('priority', ($event.target as HTMLSelectElement).value || null)"
      >
        <option v-for="p in priorities" :key="p.value" :value="p.value">
          {{ p.label }}
        </option>
      </select>

      <!-- Status -->
      <select
        class="input text-xs py-1.5 w-auto"
        :value="itemsStore.filters.is_read ?? ''"
        @change="handleFilterChange('is_read', ($event.target as HTMLSelectElement).value === '' ? null : ($event.target as HTMLSelectElement).value === 'true')"
      >
        <option value="">Alle Status</option>
        <option value="false">Ungelesen</option>
        <option value="true">Gelesen</option>
      </select>

      <!-- Archive -->
      <select
        class="input text-xs py-1.5 w-auto"
        :value="itemsStore.filters.is_archived ?? ''"
        @change="handleFilterChange('is_archived', ($event.target as HTMLSelectElement).value === '' ? null : ($event.target as HTMLSelectElement).value === 'true')"
      >
        <option value="">Aktiv</option>
        <option value="true">Archiviert</option>
      </select>

      <!-- Arbeitskreis -->
      <select
        class="input text-xs py-1.5 w-auto"
        :value="itemsStore.filters.assigned_ak ?? ''"
        @change="handleFilterChange('assigned_ak', ($event.target as HTMLSelectElement).value || null)"
      >
        <option v-for="ak in arbeitskreise" :key="ak.value" :value="ak.value">
          {{ ak.label }}
        </option>
      </select>

      <!-- Sort -->
      <select
        class="input text-xs py-1.5 w-auto"
        :value="itemsStore.filters.sort_by"
        @change="handleFilterChange('sort_by', ($event.target as HTMLSelectElement).value)"
      >
        <option v-for="s in sortOptions" :key="s.value" :value="s.value">
          {{ s.label }}
        </option>
      </select>
      <button
        type="button"
        class="btn btn-secondary px-1.5 py-1"
        :title="itemsStore.filters.sort_order === 'desc' ? 'Absteigend' : 'Aufsteigend'"
        @click="toggleSortOrder"
      >
        <ChevronUpDownIcon class="h-3.5 w-3.5" />
      </button>

      <!-- Clear Filters -->
      <button
        v-if="hasActiveFilters"
        type="button"
        class="btn btn-secondary text-xs py-1 flex items-center gap-1"
        @click="clearAllFilters"
      >
        <XMarkIcon class="h-3.5 w-3.5" />
        Reset
      </button>

      <!-- Select All -->
      <label class="flex items-center gap-1.5 ml-1">
        <input
          type="checkbox"
          class="rounded border-gray-300 h-3.5 w-3.5"
          :checked="selectedCount === itemsStore.items.length && itemsStore.items.length > 0"
          @change="emit('select-all')"
        />
        <span class="text-xs text-white">Alle</span>
      </label>

      <!-- Result count -->
      <span class="ml-auto text-xs font-semibold text-black">{{ itemsStore.total }} Ergebnisse</span>
    </div>
  </div>
</template>

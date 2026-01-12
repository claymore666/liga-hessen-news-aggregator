<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useItemsStore, useSourcesStore } from '@/stores'
import {
  MagnifyingGlassIcon,
  ChevronUpDownIcon,
  XMarkIcon,
  CheckIcon
} from '@heroicons/vue/24/outline'
import type { Priority } from '@/types'
import { startOfDay, subDays, isMonday } from 'date-fns'

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

// Date range presets
const datePresets = [
  { value: '1d', label: '1T' },
  { value: '3d', label: '3T' },
  { value: '1w', label: '1W' },
  { value: '1m', label: '1M' },
  { value: 'all', label: 'Alle' }
]

// Default: 3 days on Monday (weekend news), 1 day otherwise
const defaultPreset = computed(() => isMonday(new Date()) ? '3d' : '1d')
const selectedDatePreset = ref<string>(defaultPreset.value)

// Calculate since date from preset
const calculateSinceDate = (preset: string): string | null => {
  if (preset === 'all') return null
  const daysMap: Record<string, number> = { '1d': 1, '3d': 3, '1w': 7, '1m': 30 }
  const days = daysMap[preset]
  if (!days) return null
  const startDate = startOfDay(subDays(new Date(), days))
  return startDate.toISOString()
}

// Apply date preset
const applyDatePreset = (preset: string) => {
  selectedDatePreset.value = preset
  const since = calculateSinceDate(preset)
  itemsStore.setFilter('since', since)
  emit('search')
}

const priorities: { value: Priority; label: string; color: string }[] = [
  { value: 'high', label: 'H', color: 'bg-red-500 hover:bg-red-600' },
  { value: 'medium', label: 'M', color: 'bg-orange-500 hover:bg-orange-600' },
  { value: 'low', label: 'L', color: 'bg-yellow-500 hover:bg-yellow-600' },
  { value: 'none', label: 'N', color: 'bg-gray-400 hover:bg-gray-500' }
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

const arbeitskreise = ['AK1', 'AK2', 'AK3', 'AK4', 'AK5', 'QAG']

const sortOptions = [
  { value: 'date', label: 'Datum' },
  { value: 'priority', label: 'Priorität' },
  { value: 'source', label: 'Quelle' }
]

const hasActiveFilters = computed(() => {
  // Check if any filter differs from default
  const defaultPriorities = ['high', 'medium', 'low']
  const prioritiesChanged =
    itemsStore.filters.priorities.length !== defaultPriorities.length ||
    !defaultPriorities.every(p => itemsStore.filters.priorities.includes(p as Priority))

  return (
    prioritiesChanged ||
    itemsStore.filters.source_id ||
    itemsStore.filters.connector_type ||
    itemsStore.filters.assigned_aks.length > 0 ||
    itemsStore.filters.search ||
    itemsStore.filters.is_read !== null ||
    itemsStore.filters.is_archived !== null ||
    (selectedDatePreset.value && selectedDatePreset.value !== defaultPreset.value)
  )
})

const isPriorityActive = (priority: Priority) => {
  return itemsStore.filters.priorities.includes(priority)
}

const isAkActive = (ak: string) => {
  // Empty means all are active (no filter)
  return itemsStore.filters.assigned_aks.length === 0 || itemsStore.filters.assigned_aks.includes(ak)
}

const togglePriority = (priority: Priority) => {
  itemsStore.togglePriority(priority)
  emit('search')
}

const toggleAk = (ak: string) => {
  // If currently showing all (empty array), clicking one AK means "only show this one"
  if (itemsStore.filters.assigned_aks.length === 0) {
    // Enable only the clicked one
    itemsStore.filters.assigned_aks = [ak]
  } else if (itemsStore.filters.assigned_aks.includes(ak)) {
    // Remove this AK
    const newAks = itemsStore.filters.assigned_aks.filter(a => a !== ak)
    // If removing leaves empty, that means "all"
    itemsStore.filters.assigned_aks = newAks
  } else {
    // Add this AK
    itemsStore.filters.assigned_aks.push(ak)
  }
  emit('search')
}

const applySearch = () => {
  itemsStore.setFilter('search', searchQuery.value || null)
  emit('search')
}

const clearAllFilters = () => {
  searchQuery.value = ''
  itemsStore.clearFilters()
  // Reset to default date preset
  applyDatePreset(defaultPreset.value)
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

// Apply default date preset on mount
onMounted(() => {
  const since = calculateSinceDate(defaultPreset.value)
  itemsStore.setFilter('since', since)
})
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

      <!-- Date Range Presets -->
      <div class="flex items-center gap-0.5">
        <button
          v-for="preset in datePresets"
          :key="preset.value"
          type="button"
          class="px-1.5 py-0.5 text-xs font-medium rounded transition-all"
          :class="selectedDatePreset === preset.value
            ? 'bg-blue-600 hover:bg-blue-700 text-white'
            : 'bg-gray-200 text-gray-600 hover:bg-gray-300'"
          @click="applyDatePreset(preset.value)"
        >
          {{ preset.label }}
        </button>
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

      <!-- Priority Toggle Buttons -->
      <div class="flex items-center gap-0.5">
        <span class="text-xs text-white mr-1">Prio:</span>
        <button
          v-for="p in priorities"
          :key="p.value"
          type="button"
          class="px-1.5 py-0.5 text-xs font-medium rounded transition-all"
          :class="isPriorityActive(p.value)
            ? p.color + ' text-white'
            : 'bg-gray-200 text-gray-500 hover:bg-gray-300'"
          :title="p.value"
          @click="togglePriority(p.value)"
        >
          {{ p.label }}
        </button>
      </div>

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

      <!-- Arbeitskreis Toggle Buttons -->
      <div class="flex items-center gap-0.5">
        <span class="text-xs text-white mr-1">AK:</span>
        <button
          v-for="ak in arbeitskreise"
          :key="ak"
          type="button"
          class="px-1.5 py-0.5 text-xs font-medium rounded transition-all"
          :class="isAkActive(ak)
            ? 'bg-purple-500 hover:bg-purple-600 text-white'
            : 'bg-gray-200 text-gray-500 hover:bg-gray-300'"
          @click="toggleAk(ak)"
        >
          {{ ak }}
        </button>
      </div>

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
      <label class="flex items-center gap-1.5 ml-1" title="Alle auswählen">
        <input
          type="checkbox"
          class="rounded border-gray-300 h-3.5 w-3.5"
          :checked="selectedCount === itemsStore.items.length && itemsStore.items.length > 0"
          @change="emit('select-all')"
        />
        <span class="text-xs text-white">Alle auswählen</span>
      </label>

      <!-- Result count -->
      <span class="ml-auto text-xs font-semibold text-black">{{ itemsStore.total }} Ergebnisse</span>
    </div>
  </div>
</template>

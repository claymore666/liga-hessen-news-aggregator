<script setup lang="ts">
import { onMounted, computed, ref, watch } from 'vue'
import { RouterLink, useRouter, useRoute } from 'vue-router'
import { useItemsStore, useSourcesStore, useStatsStore } from '@/stores'
import {
  NewspaperIcon,
  RssIcon,
  ExclamationTriangleIcon,
  ClockIcon,
  ArrowPathIcon,
  MagnifyingGlassIcon,
  XMarkIcon,
  ChevronUpDownIcon
} from '@heroicons/vue/24/outline'
import PriorityBadge from '@/components/PriorityBadge.vue'
import SourceIcon from '@/components/SourceIcon.vue'
import type { Priority } from '@/types'
import { formatDistanceToNow, startOfDay, subDays, subWeeks, subMonths, isMonday } from 'date-fns'
import { de } from 'date-fns/locale'

const router = useRouter()
const route = useRoute()
const itemsStore = useItemsStore()
const sourcesStore = useSourcesStore()
const statsStore = useStatsStore()

const searchQuery = ref('')
const page = ref(1)
const pageSize = 50

// Filter options - Toggle button style
const priorityButtons: { value: Priority; label: string; color: string }[] = [
  { value: 'high', label: 'H', color: 'bg-red-500 hover:bg-red-600' },
  { value: 'medium', label: 'M', color: 'bg-orange-500 hover:bg-orange-600' },
  { value: 'low', label: 'L', color: 'bg-yellow-500 hover:bg-yellow-600' },
  { value: 'none', label: 'N', color: 'bg-gray-400 hover:bg-gray-500' }
]

const akButtons = ['AK1', 'AK2', 'AK3', 'AK4', 'AK5', 'QAG']

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

const toggleAk = (ak: string) => {
  if (itemsStore.filters.assigned_aks.length === 0) {
    itemsStore.filters.assigned_aks = [ak]
  } else if (itemsStore.filters.assigned_aks.includes(ak)) {
    itemsStore.filters.assigned_aks = itemsStore.filters.assigned_aks.filter(a => a !== ak)
  } else {
    itemsStore.filters.assigned_aks.push(ak)
  }
}

const sortOptions = [
  { value: 'date', label: 'Datum' },
  { value: 'priority', label: 'Priorität' },
  { value: 'source', label: 'Quelle' }
]

// Date range presets
const datePresets = [
  { value: '1d', label: '1 Tag', days: 1 },
  { value: '3d', label: '3 Tage', days: 3 },
  { value: '1w', label: '1 Woche', days: 7 },
  { value: '1m', label: '1 Monat', days: 30 },
  { value: 'all', label: 'Alle', days: null }
]

// Default: 3 days on Monday (weekend news), 1 day otherwise
const defaultPreset = computed(() => isMonday(new Date()) ? '3d' : '1d')
const selectedDatePreset = ref<string | null>(null)

// Calculate since date from preset
const calculateSinceDate = (preset: string): string | null => {
  if (preset === 'all') return null

  const presetConfig = datePresets.find(p => p.value === preset)
  if (!presetConfig?.days) return null

  const now = new Date()
  const startDate = startOfDay(subDays(now, presetConfig.days))
  return startDate.toISOString()
}

// Apply date preset
const applyDatePreset = (preset: string) => {
  selectedDatePreset.value = preset
  const since = calculateSinceDate(preset)
  itemsStore.setFilter('since', since)
}

// Computed
const hasActiveFilters = computed(() => {
  // Check if priorities differ from default [high, medium, low]
  const defaultPriorities = ['high', 'medium', 'low']
  const prioritiesChanged = itemsStore.filters.priorities.length !== defaultPriorities.length ||
    !defaultPriorities.every(p => itemsStore.filters.priorities.includes(p as Priority))

  return (
    prioritiesChanged ||
    itemsStore.filters.source_id ||
    itemsStore.filters.connector_type ||
    itemsStore.filters.assigned_aks.length > 0 ||
    itemsStore.filters.search ||
    (selectedDatePreset.value && selectedDatePreset.value !== defaultPreset.value)
  )
})

const formatTime = (date: string | null) => {
  if (!date) return 'Nie'
  return formatDistanceToNow(new Date(date), { addSuffix: true, locale: de })
}

// Methods
const loadItems = async () => {
  await itemsStore.fetchItems({ page: page.value, page_size: pageSize })
}

const applySearch = () => {
  itemsStore.setFilter('search', searchQuery.value || null)
  page.value = 1
  loadItems()
}

const clearAllFilters = () => {
  searchQuery.value = ''
  itemsStore.clearFilters()
  // Reset to default date preset
  applyDatePreset(defaultPreset.value)
  page.value = 1
  loadItems()
}

const toggleSortOrder = () => {
  const newOrder = itemsStore.filters.sort_order === 'desc' ? 'asc' : 'desc'
  itemsStore.setFilter('sort_order', newOrder)
  loadItems()
}

// Watch for filter changes (except search which has its own button)
watch(
  () => [
    itemsStore.filters.priorities,
    itemsStore.filters.source_id,
    itemsStore.filters.connector_type,
    itemsStore.filters.assigned_aks,
    itemsStore.filters.sort_by,
    itemsStore.filters.since
  ],
  () => {
    page.value = 1
    loadItems()
  },
  { deep: true }
)

// Load URL params on mount
onMounted(async () => {
  // Parse URL params
  if (route.query.priority) {
    const priorities = (route.query.priority as string).split(',') as Priority[]
    itemsStore.filters.priorities = priorities
  }
  if (route.query.connector_type) {
    itemsStore.setFilter('connector_type', route.query.connector_type as string)
  }
  if (route.query.assigned_ak) {
    const aks = (route.query.assigned_ak as string).split(',')
    itemsStore.filters.assigned_aks = aks
  }
  if (route.query.source_id) {
    itemsStore.setFilter('source_id', parseInt(route.query.source_id as string))
  }
  if (route.query.search) {
    searchQuery.value = route.query.search as string
    itemsStore.setFilter('search', route.query.search as string)
  }

  // Apply date preset from URL or use smart default
  const presetFromUrl = route.query.date_range as string | undefined
  const presetToApply = presetFromUrl && datePresets.some(p => p.value === presetFromUrl)
    ? presetFromUrl
    : defaultPreset.value
  applyDatePreset(presetToApply)

  await Promise.all([
    statsStore.fetchStats(),
    loadItems(),
    sourcesStore.fetchSources()
  ])
})

// Update URL when filters change
watch(
  [() => itemsStore.filters, selectedDatePreset],
  ([filters, datePreset]) => {
    const query: Record<string, string> = {}
    if (filters.priorities.length > 0) query.priority = filters.priorities.join(',')
    if (filters.connector_type) query.connector_type = filters.connector_type
    if (filters.assigned_aks.length > 0) query.assigned_ak = filters.assigned_aks.join(',')
    if (filters.source_id) query.source_id = String(filters.source_id)
    if (filters.search) query.search = filters.search
    if (datePreset && datePreset !== defaultPreset.value) query.date_range = datePreset

    router.replace({ query })
  },
  { deep: true }
)
</script>

<template>
  <div class="space-y-3">
    <div class="flex items-center justify-between">
      <h1 class="text-xl font-bold text-gray-900">Dashboard</h1>
      <p class="text-sm text-gray-500">Liga der Freien Wohlfahrtspflege Hessen</p>
    </div>

    <!-- Stats Cards -->
    <div class="grid grid-cols-2 gap-2 lg:grid-cols-4">
      <div class="rounded-lg border border-blue-300 bg-blue-100 py-2 px-3">
        <div class="flex items-center gap-2">
          <div class="rounded bg-blue-200 p-1.5">
            <NewspaperIcon class="h-4 w-4 text-blue-700" />
          </div>
          <div>
            <p class="text-xs text-blue-600">Nachrichten</p>
            <p class="text-lg font-semibold text-gray-900">{{ statsStore.stats?.total_items ?? '-' }}</p>
          </div>
        </div>
      </div>

      <div class="rounded-lg border border-blue-300 bg-blue-100 py-2 px-3">
        <div class="flex items-center gap-2">
          <div class="rounded bg-yellow-200 p-1.5">
            <ExclamationTriangleIcon class="h-4 w-4 text-yellow-700" />
          </div>
          <div>
            <p class="text-xs text-blue-600">Ungelesen</p>
            <p class="text-lg font-semibold text-gray-900">{{ statsStore.stats?.unread_items ?? '-' }}</p>
          </div>
        </div>
      </div>

      <div class="rounded-lg border border-blue-300 bg-blue-100 py-2 px-3">
        <div class="flex items-center gap-2">
          <div class="rounded bg-green-200 p-1.5">
            <RssIcon class="h-4 w-4 text-green-700" />
          </div>
          <div>
            <p class="text-xs text-blue-600">Quellen</p>
            <p class="text-lg font-semibold text-gray-900">{{ statsStore.stats?.sources_count ?? '-' }}</p>
          </div>
        </div>
      </div>

      <div class="rounded-lg border border-blue-300 bg-blue-100 py-2 px-3">
        <div class="flex items-center gap-2">
          <div class="rounded bg-purple-200 p-1.5">
            <ClockIcon class="h-4 w-4 text-purple-700" />
          </div>
          <div>
            <p class="text-xs text-blue-600">Letzter Abruf</p>
            <p class="text-sm font-semibold text-gray-900">{{ formatTime(statsStore.stats?.last_fetch_at ?? null) }}</p>
          </div>
        </div>
      </div>
    </div>

    <!-- Priority Summary -->
    <div class="grid grid-cols-4 gap-2">
      <button
        class="rounded-lg py-1.5 px-3 bg-red-500 hover:bg-red-600 transition-colors cursor-pointer text-center"
        :class="itemsStore.filters.priorities.includes('high') ? 'ring-2 ring-offset-1 ring-red-700' : 'opacity-60'"
        @click="itemsStore.togglePriority('high')"
      >
        <span class="text-lg font-bold text-white">{{ statsStore.stats?.items_by_priority?.high ?? 0 }}</span>
        <span class="text-xs text-red-100 ml-1">Hoch</span>
      </button>
      <button
        class="rounded-lg py-1.5 px-3 bg-orange-500 hover:bg-orange-600 transition-colors cursor-pointer text-center"
        :class="itemsStore.filters.priorities.includes('medium') ? 'ring-2 ring-offset-1 ring-orange-700' : 'opacity-60'"
        @click="itemsStore.togglePriority('medium')"
      >
        <span class="text-lg font-bold text-white">{{ statsStore.stats?.items_by_priority?.medium ?? 0 }}</span>
        <span class="text-xs text-orange-100 ml-1">Mittel</span>
      </button>
      <button
        class="rounded-lg py-1.5 px-3 bg-yellow-400 hover:bg-yellow-500 transition-colors cursor-pointer text-center"
        :class="itemsStore.filters.priorities.includes('low') ? 'ring-2 ring-offset-1 ring-yellow-600' : 'opacity-60'"
        @click="itemsStore.togglePriority('low')"
      >
        <span class="text-lg font-bold text-gray-900">{{ statsStore.stats?.items_by_priority?.low ?? 0 }}</span>
        <span class="text-xs text-yellow-800 ml-1">Niedrig</span>
      </button>
      <button
        class="rounded-lg py-1.5 px-3 bg-blue-500 hover:bg-blue-600 transition-colors cursor-pointer text-center"
        :class="itemsStore.filters.priorities.includes('none') ? 'ring-2 ring-offset-1 ring-blue-700' : 'opacity-60'"
        @click="itemsStore.togglePriority('none')"
      >
        <span class="text-lg font-bold text-white">{{ statsStore.stats?.items_by_priority?.none ?? 0 }}</span>
        <span class="text-xs text-blue-100 ml-1">Keine</span>
      </button>
    </div>

    <!-- News Items with Filters -->
    <div class="rounded-lg border border-blue-300 overflow-hidden">
      <!-- Filter Bar (always visible) -->
      <div class="flex flex-wrap items-center gap-2 px-4 py-2 bg-blue-400 border-b border-blue-500">
        <!-- Search -->
        <div class="relative">
          <input
            v-model="searchQuery"
            type="text"
            placeholder="Suchen..."
            class="input pl-9 text-sm w-40"
            @keyup.enter="applySearch"
          />
          <MagnifyingGlassIcon class="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
        </div>

        <!-- Connector Type -->
        <select
          class="input text-sm w-auto"
          :value="itemsStore.filters.connector_type ?? ''"
          @change="itemsStore.setFilter('connector_type', ($event.target as HTMLSelectElement).value || null)"
        >
          <option v-for="t in connectorTypes" :key="t.value" :value="t.value">
            {{ t.label }}
          </option>
        </select>

        <!-- Source -->
        <select
          class="input text-sm w-auto max-w-48"
          :value="itemsStore.filters.source_id ?? ''"
          @change="itemsStore.setFilter('source_id', parseInt(($event.target as HTMLSelectElement).value) || null)"
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
            v-for="p in priorityButtons"
            :key="p.value"
            type="button"
            class="px-1.5 py-0.5 text-xs font-medium rounded transition-all"
            :class="itemsStore.filters.priorities.includes(p.value)
              ? p.color + ' text-white'
              : 'bg-gray-200 text-gray-500 hover:bg-gray-300'"
            :title="p.value"
            @click="itemsStore.togglePriority(p.value)"
          >
            {{ p.label }}
          </button>
        </div>

        <!-- Arbeitskreis Toggle Buttons -->
        <div class="flex items-center gap-0.5">
          <span class="text-xs text-white mr-1">AK:</span>
          <button
            v-for="ak in akButtons"
            :key="ak"
            type="button"
            class="px-1.5 py-0.5 text-xs font-medium rounded transition-all"
            :class="itemsStore.filters.assigned_aks.length === 0 || itemsStore.filters.assigned_aks.includes(ak)
              ? 'bg-purple-500 hover:bg-purple-600 text-white'
              : 'bg-gray-200 text-gray-500 hover:bg-gray-300'"
            @click="toggleAk(ak)"
          >
            {{ ak }}
          </button>
        </div>

        <!-- Sort -->
        <select
          class="input text-sm w-auto"
          :value="itemsStore.filters.sort_by"
          @change="itemsStore.setFilter('sort_by', ($event.target as HTMLSelectElement).value)"
        >
          <option v-for="s in sortOptions" :key="s.value" :value="s.value">
            {{ s.label }}
          </option>
        </select>
        <button
          type="button"
          class="btn btn-secondary px-2"
          :title="itemsStore.filters.sort_order === 'desc' ? 'Absteigend' : 'Aufsteigend'"
          @click="toggleSortOrder"
        >
          <ChevronUpDownIcon class="h-4 w-4" />
        </button>

        <!-- Date Range Presets -->
        <div class="flex items-center gap-1 ml-2 border-l border-blue-500 pl-2">
          <button
            v-for="preset in datePresets"
            :key="preset.value"
            type="button"
            class="px-2 py-1 text-xs font-medium rounded transition-colors"
            :class="selectedDatePreset === preset.value
              ? 'bg-blue-700 text-white'
              : 'bg-blue-300 text-blue-900 hover:bg-blue-500 hover:text-white'"
            @click="applyDatePreset(preset.value)"
          >
            {{ preset.label }}
          </button>
        </div>

        <!-- Clear Filters -->
        <button
          v-if="hasActiveFilters"
          type="button"
          class="btn btn-secondary text-sm flex items-center gap-1"
          @click="clearAllFilters"
        >
          <XMarkIcon class="h-4 w-4" />
          Reset
        </button>

        <!-- Result count -->
        <span class="ml-auto text-sm font-semibold text-black">{{ itemsStore.total }} Ergebnisse</span>
      </div>

      <!-- Loading -->
      <div v-if="itemsStore.loading" class="flex items-center justify-center py-6">
        <ArrowPathIcon class="h-6 w-6 animate-spin text-gray-400" />
      </div>

      <!-- Empty State -->
      <div v-else-if="itemsStore.items.length === 0" class="py-6 text-center text-gray-500">
        Keine Nachrichten gefunden
      </div>

      <!-- Scrollable News List -->
      <div v-else class="max-h-[calc(100vh-420px)] min-h-[200px] overflow-y-auto">
        <ul>
          <li v-for="(item, index) in itemsStore.items" :key="item.id">
            <RouterLink
              :to="`/items/${item.id}`"
              class="block py-2 px-4 transition-colors hover:bg-yellow-100"
              :class="index % 2 === 1 ? 'bg-blue-200' : 'bg-blue-100'"
            >
              <div class="flex items-center gap-2">
                <PriorityBadge :priority="item.priority" class="flex-shrink-0" />
                <div class="min-w-0 flex-1">
                  <p
                    class="truncate text-sm"
                    :class="item.is_read ? 'text-gray-500' : 'font-medium text-gray-900'"
                  >
                    {{ item.title }}
                  </p>
                </div>
                <span class="flex items-center gap-1 text-xs text-black flex-shrink-0">
                  <SourceIcon v-if="item.channel" :connector-type="item.channel.connector_type" size="sm" />
                  <span class="max-w-20 truncate">{{ (item.metadata as Record<string, unknown> | undefined)?.source_domain ?? item.source?.name ?? '' }}</span>
                  <span class="text-gray-500">{{ formatTime(item.published_at) }}</span>
                  <span v-if="item.metadata?.llm_analysis?.assigned_ak" class="rounded bg-blue-300 px-1 text-xs font-medium text-black">
                    {{ item.metadata.llm_analysis.assigned_ak }}
                  </span>
                </span>
              </div>
            </RouterLink>
          </li>
        </ul>
      </div>

      <!-- Pagination -->
      <div v-if="itemsStore.total > pageSize" class="flex items-center justify-between border-t border-blue-300 bg-blue-100 px-4 py-2">
        <p class="text-sm text-gray-500">
          {{ (page - 1) * pageSize + 1 }} - {{ Math.min(page * pageSize, itemsStore.total) }} von {{ itemsStore.total }}
        </p>
        <div class="flex gap-2">
          <button
            type="button"
            class="btn btn-secondary text-sm"
            :disabled="page === 1"
            @click="page--; loadItems()"
          >
            Zurück
          </button>
          <button
            type="button"
            class="btn btn-secondary text-sm"
            :disabled="page * pageSize >= itemsStore.total"
            @click="page++; loadItems()"
          >
            Weiter
          </button>
        </div>
      </div>
    </div>

    <!-- Quick Actions -->
    <div class="flex flex-wrap gap-2">
      <button
        type="button"
        class="btn btn-primary text-sm"
        :disabled="sourcesStore.fetchingSourceId !== null"
        @click="sourcesStore.fetchAllChannels()"
      >
        <ArrowPathIcon
          class="mr-1.5 h-4 w-4"
          :class="{ 'animate-spin': sourcesStore.fetchingSourceId !== null }"
        />
        Alle abrufen
      </button>
      <RouterLink to="/sources" class="btn btn-secondary text-sm">
        Quellen
      </RouterLink>
      <RouterLink to="/rules" class="btn btn-secondary text-sm">
        Regeln
      </RouterLink>
    </div>
  </div>
</template>

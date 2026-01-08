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
import { formatDistanceToNow } from 'date-fns'
import { de } from 'date-fns/locale'

const router = useRouter()
const route = useRoute()
const itemsStore = useItemsStore()
const sourcesStore = useSourcesStore()
const statsStore = useStatsStore()

const searchQuery = ref('')
const page = ref(1)
const pageSize = 50

// Filter options
const priorities = [
  { value: '', label: 'Alle Prioritäten' },
  { value: 'critical', label: 'Kritisch' },
  { value: 'high', label: 'Hoch' },
  { value: 'medium', label: 'Mittel' },
  { value: 'low', label: 'Niedrig' }
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
  { value: 'AK1', label: 'AK1 - Grundsatz' },
  { value: 'AK2', label: 'AK2 - Migration' },
  { value: 'AK3', label: 'AK3 - Pflege' },
  { value: 'AK4', label: 'AK4 - Eingliederung' },
  { value: 'AK5', label: 'AK5 - Kinder/Jugend' },
  { value: 'QAG', label: 'QAG - Querschnitt' }
]

const sortOptions = [
  { value: 'date', label: 'Datum' },
  { value: 'priority', label: 'Priorität' },
  { value: 'source', label: 'Quelle' }
]

// Computed
const hasActiveFilters = computed(() => {
  return (
    itemsStore.filters.priority ||
    itemsStore.filters.source_id ||
    itemsStore.filters.connector_type ||
    itemsStore.filters.assigned_ak ||
    itemsStore.filters.search
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
    itemsStore.filters.priority,
    itemsStore.filters.source_id,
    itemsStore.filters.connector_type,
    itemsStore.filters.assigned_ak,
    itemsStore.filters.sort_by
  ],
  () => {
    page.value = 1
    loadItems()
  }
)

// Load URL params on mount
onMounted(async () => {
  // Parse URL params
  if (route.query.priority) {
    itemsStore.setFilter('priority', route.query.priority as string)
  }
  if (route.query.connector_type) {
    itemsStore.setFilter('connector_type', route.query.connector_type as string)
  }
  if (route.query.assigned_ak) {
    itemsStore.setFilter('assigned_ak', route.query.assigned_ak as string)
  }
  if (route.query.source_id) {
    itemsStore.setFilter('source_id', parseInt(route.query.source_id as string))
  }
  if (route.query.search) {
    searchQuery.value = route.query.search as string
    itemsStore.setFilter('search', route.query.search as string)
  }

  await Promise.all([
    statsStore.fetchStats(),
    loadItems(),
    sourcesStore.fetchSources()
  ])
})

// Update URL when filters change
watch(
  () => itemsStore.filters,
  (filters) => {
    const query: Record<string, string> = {}
    if (filters.priority) query.priority = filters.priority
    if (filters.connector_type) query.connector_type = filters.connector_type
    if (filters.assigned_ak) query.assigned_ak = filters.assigned_ak
    if (filters.source_id) query.source_id = String(filters.source_id)
    if (filters.search) query.search = filters.search

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
      <div class="card py-2 px-3">
        <div class="flex items-center gap-2">
          <div class="rounded bg-liga-100 p-1.5">
            <NewspaperIcon class="h-4 w-4 text-liga-600" />
          </div>
          <div>
            <p class="text-xs text-gray-500">Nachrichten</p>
            <p class="text-lg font-semibold text-gray-900">{{ statsStore.stats?.total_items ?? '-' }}</p>
          </div>
        </div>
      </div>

      <div class="card py-2 px-3">
        <div class="flex items-center gap-2">
          <div class="rounded bg-yellow-100 p-1.5">
            <ExclamationTriangleIcon class="h-4 w-4 text-yellow-600" />
          </div>
          <div>
            <p class="text-xs text-gray-500">Ungelesen</p>
            <p class="text-lg font-semibold text-gray-900">{{ statsStore.stats?.unread_items ?? '-' }}</p>
          </div>
        </div>
      </div>

      <div class="card py-2 px-3">
        <div class="flex items-center gap-2">
          <div class="rounded bg-green-100 p-1.5">
            <RssIcon class="h-4 w-4 text-green-600" />
          </div>
          <div>
            <p class="text-xs text-gray-500">Quellen</p>
            <p class="text-lg font-semibold text-gray-900">{{ statsStore.stats?.sources_count ?? '-' }}</p>
          </div>
        </div>
      </div>

      <div class="card py-2 px-3">
        <div class="flex items-center gap-2">
          <div class="rounded bg-purple-100 p-1.5">
            <ClockIcon class="h-4 w-4 text-purple-600" />
          </div>
          <div>
            <p class="text-xs text-gray-500">Letzter Abruf</p>
            <p class="text-sm font-semibold text-gray-900">{{ formatTime(statsStore.stats?.last_fetch_at ?? null) }}</p>
          </div>
        </div>
      </div>
    </div>

    <!-- Priority Summary -->
    <div class="grid grid-cols-4 gap-2">
      <button
        class="rounded-lg py-1.5 px-3 bg-red-500 hover:bg-red-600 transition-colors cursor-pointer text-center"
        :class="itemsStore.filters.priority === 'critical' ? 'ring-2 ring-offset-1 ring-red-700' : ''"
        @click="itemsStore.setFilter('priority', itemsStore.filters.priority === 'critical' ? null : 'critical')"
      >
        <span class="text-lg font-bold text-white">{{ statsStore.stats?.items_by_priority?.critical ?? 0 }}</span>
        <span class="text-xs text-red-100 ml-1">Kritisch</span>
      </button>
      <button
        class="rounded-lg py-1.5 px-3 bg-orange-500 hover:bg-orange-600 transition-colors cursor-pointer text-center"
        :class="itemsStore.filters.priority === 'high' ? 'ring-2 ring-offset-1 ring-orange-700' : ''"
        @click="itemsStore.setFilter('priority', itemsStore.filters.priority === 'high' ? null : 'high')"
      >
        <span class="text-lg font-bold text-white">{{ statsStore.stats?.items_by_priority?.high ?? 0 }}</span>
        <span class="text-xs text-orange-100 ml-1">Hoch</span>
      </button>
      <button
        class="rounded-lg py-1.5 px-3 bg-yellow-400 hover:bg-yellow-500 transition-colors cursor-pointer text-center"
        :class="itemsStore.filters.priority === 'medium' ? 'ring-2 ring-offset-1 ring-yellow-600' : ''"
        @click="itemsStore.setFilter('priority', itemsStore.filters.priority === 'medium' ? null : 'medium')"
      >
        <span class="text-lg font-bold text-gray-900">{{ statsStore.stats?.items_by_priority?.medium ?? 0 }}</span>
        <span class="text-xs text-yellow-800 ml-1">Mittel</span>
      </button>
      <button
        class="rounded-lg py-1.5 px-3 bg-green-500 hover:bg-green-600 transition-colors cursor-pointer text-center"
        :class="itemsStore.filters.priority === 'low' ? 'ring-2 ring-offset-1 ring-green-700' : ''"
        @click="itemsStore.setFilter('priority', itemsStore.filters.priority === 'low' ? null : 'low')"
      >
        <span class="text-lg font-bold text-white">{{ statsStore.stats?.items_by_priority?.low ?? 0 }}</span>
        <span class="text-xs text-green-100 ml-1">Niedrig</span>
      </button>
    </div>

    <!-- News Items with Filters -->
    <div class="card py-3 px-4">
      <!-- Filter Bar (always visible) -->
      <div class="flex flex-wrap items-center gap-2 mb-2">
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

        <!-- Priority -->
        <select
          class="input text-sm w-auto"
          :value="itemsStore.filters.priority ?? ''"
          @change="itemsStore.setFilter('priority', ($event.target as HTMLSelectElement).value || null)"
        >
          <option v-for="p in priorities" :key="p.value" :value="p.value">
            {{ p.label }}
          </option>
        </select>

        <!-- Arbeitskreis -->
        <select
          class="input text-sm w-auto"
          :value="itemsStore.filters.assigned_ak ?? ''"
          @change="itemsStore.setFilter('assigned_ak', ($event.target as HTMLSelectElement).value || null)"
        >
          <option v-for="ak in arbeitskreise" :key="ak.value" :value="ak.value">
            {{ ak.label }}
          </option>
        </select>

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
        <span class="ml-auto text-xs text-gray-500">{{ itemsStore.total }} Ergebnisse</span>
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
      <div v-else class="max-h-[calc(100vh-340px)] min-h-[300px] overflow-y-auto -mx-4">
        <ul class="divide-y divide-gray-100">
          <li v-for="item in itemsStore.items" :key="item.id">
            <RouterLink
              :to="`/items/${item.id}`"
              class="block py-2 hover:bg-gray-50 px-4 transition-colors"
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
                <span class="flex items-center gap-1 text-xs text-gray-400 flex-shrink-0">
                  <SourceIcon v-if="item.source" :connector-type="item.source.connector_type" size="sm" />
                  {{ formatTime(item.published_at) }}
                  <span v-if="item.metadata?.llm_analysis?.assigned_ak" class="rounded bg-gray-100 px-1 text-xs">
                    {{ item.metadata.llm_analysis.assigned_ak }}
                  </span>
                </span>
              </div>
            </RouterLink>
          </li>
        </ul>
      </div>

      <!-- Pagination -->
      <div v-if="itemsStore.total > pageSize" class="mt-2 flex items-center justify-between border-t pt-2 mx-0">
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
        :disabled="sourcesStore.fetching !== null"
        @click="sourcesStore.triggerFetchAll()"
      >
        <ArrowPathIcon
          class="mr-1.5 h-4 w-4"
          :class="{ 'animate-spin': sourcesStore.fetching !== null }"
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

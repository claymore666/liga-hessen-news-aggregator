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
  FunnelIcon,
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

const showFilters = ref(false)
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
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-gray-900">Dashboard</h1>
      <p class="mt-1 text-sm text-gray-500">
        Nachrichtenanalyse für die Liga der Freien Wohlfahrtspflege Hessen
      </p>
    </div>

    <!-- Stats Cards -->
    <div class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <div class="card">
        <div class="flex items-center gap-4">
          <div class="rounded-lg bg-liga-100 p-3">
            <NewspaperIcon class="h-6 w-6 text-liga-600" />
          </div>
          <div>
            <p class="text-sm font-medium text-gray-500">Nachrichten</p>
            <p class="text-2xl font-semibold text-gray-900">
              {{ statsStore.stats?.total_items ?? '-' }}
            </p>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="flex items-center gap-4">
          <div class="rounded-lg bg-yellow-100 p-3">
            <ExclamationTriangleIcon class="h-6 w-6 text-yellow-600" />
          </div>
          <div>
            <p class="text-sm font-medium text-gray-500">Ungelesen</p>
            <p class="text-2xl font-semibold text-gray-900">
              {{ statsStore.stats?.unread_items ?? '-' }}
            </p>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="flex items-center gap-4">
          <div class="rounded-lg bg-green-100 p-3">
            <RssIcon class="h-6 w-6 text-green-600" />
          </div>
          <div>
            <p class="text-sm font-medium text-gray-500">Quellen</p>
            <p class="text-2xl font-semibold text-gray-900">
              {{ statsStore.stats?.sources_count ?? '-' }}
            </p>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="flex items-center gap-4">
          <div class="rounded-lg bg-purple-100 p-3">
            <ClockIcon class="h-6 w-6 text-purple-600" />
          </div>
          <div>
            <p class="text-sm font-medium text-gray-500">Letzter Abruf</p>
            <p class="text-sm font-semibold text-gray-900">
              {{ formatTime(statsStore.stats?.last_fetch_at ?? null) }}
            </p>
          </div>
        </div>
      </div>
    </div>

    <!-- Priority Summary -->
    <div class="card">
      <h2 class="text-lg font-medium text-gray-900">Nach Priorität</h2>
      <div class="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <button
          class="rounded-lg bg-red-50 p-4 text-center hover:bg-red-100 transition-colors cursor-pointer"
          :class="{ 'ring-2 ring-red-500': itemsStore.filters.priority === 'critical' }"
          @click="itemsStore.setFilter('priority', itemsStore.filters.priority === 'critical' ? null : 'critical')"
        >
          <p class="text-3xl font-bold text-red-600">
            {{ statsStore.stats?.items_by_priority?.critical ?? 0 }}
          </p>
          <p class="text-sm text-red-700">Kritisch</p>
        </button>
        <button
          class="rounded-lg bg-orange-50 p-4 text-center hover:bg-orange-100 transition-colors cursor-pointer"
          :class="{ 'ring-2 ring-orange-500': itemsStore.filters.priority === 'high' }"
          @click="itemsStore.setFilter('priority', itemsStore.filters.priority === 'high' ? null : 'high')"
        >
          <p class="text-3xl font-bold text-orange-600">
            {{ statsStore.stats?.items_by_priority?.high ?? 0 }}
          </p>
          <p class="text-sm text-orange-700">Hoch</p>
        </button>
        <button
          class="rounded-lg bg-yellow-50 p-4 text-center hover:bg-yellow-100 transition-colors cursor-pointer"
          :class="{ 'ring-2 ring-yellow-500': itemsStore.filters.priority === 'medium' }"
          @click="itemsStore.setFilter('priority', itemsStore.filters.priority === 'medium' ? null : 'medium')"
        >
          <p class="text-3xl font-bold text-yellow-600">
            {{ statsStore.stats?.items_by_priority?.medium ?? 0 }}
          </p>
          <p class="text-sm text-yellow-700">Mittel</p>
        </button>
        <button
          class="rounded-lg bg-green-50 p-4 text-center hover:bg-green-100 transition-colors cursor-pointer"
          :class="{ 'ring-2 ring-green-500': itemsStore.filters.priority === 'low' }"
          @click="itemsStore.setFilter('priority', itemsStore.filters.priority === 'low' ? null : 'low')"
        >
          <p class="text-3xl font-bold text-green-600">
            {{ statsStore.stats?.items_by_priority?.low ?? 0 }}
          </p>
          <p class="text-sm text-green-700">Niedrig</p>
        </button>
      </div>
    </div>

    <!-- News Items with Filters -->
    <div class="card">
      <!-- Header with search and filter toggle -->
      <div class="flex flex-wrap items-center justify-between gap-4">
        <h2 class="text-lg font-medium text-gray-900">Aktuelle Nachrichten</h2>
        <div class="flex items-center gap-2">
          <!-- Search -->
          <div class="relative">
            <input
              v-model="searchQuery"
              type="text"
              placeholder="Suchen..."
              class="input pl-9 w-48"
              @keyup.enter="applySearch"
            />
            <MagnifyingGlassIcon class="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
          </div>
          <button
            type="button"
            class="btn btn-secondary"
            :class="{ 'bg-liga-100': showFilters }"
            @click="showFilters = !showFilters"
          >
            <FunnelIcon class="mr-1 h-4 w-4" />
            Filter
            <span v-if="hasActiveFilters" class="ml-1 rounded-full bg-liga-500 px-1.5 py-0.5 text-xs text-white">
              aktiv
            </span>
          </button>
        </div>
      </div>

      <!-- Filter Bar -->
      <div v-if="showFilters" class="mt-4 p-4 bg-gray-50 rounded-lg">
        <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          <!-- Connector Type -->
          <div>
            <label class="block text-xs font-medium text-gray-700 mb-1">Typ</label>
            <select
              class="input text-sm"
              :value="itemsStore.filters.connector_type ?? ''"
              @change="itemsStore.setFilter('connector_type', ($event.target as HTMLSelectElement).value || null)"
            >
              <option v-for="t in connectorTypes" :key="t.value" :value="t.value">
                {{ t.label }}
              </option>
            </select>
          </div>

          <!-- Source -->
          <div>
            <label class="block text-xs font-medium text-gray-700 mb-1">Quelle</label>
            <select
              class="input text-sm"
              :value="itemsStore.filters.source_id ?? ''"
              @change="itemsStore.setFilter('source_id', parseInt(($event.target as HTMLSelectElement).value) || null)"
            >
              <option value="">Alle Quellen</option>
              <option v-for="s in sourcesStore.sources" :key="s.id" :value="s.id">
                {{ s.name }}
              </option>
            </select>
          </div>

          <!-- Priority -->
          <div>
            <label class="block text-xs font-medium text-gray-700 mb-1">Priorität</label>
            <select
              class="input text-sm"
              :value="itemsStore.filters.priority ?? ''"
              @change="itemsStore.setFilter('priority', ($event.target as HTMLSelectElement).value || null)"
            >
              <option v-for="p in priorities" :key="p.value" :value="p.value">
                {{ p.label }}
              </option>
            </select>
          </div>

          <!-- Arbeitskreis -->
          <div>
            <label class="block text-xs font-medium text-gray-700 mb-1">Arbeitskreis</label>
            <select
              class="input text-sm"
              :value="itemsStore.filters.assigned_ak ?? ''"
              @change="itemsStore.setFilter('assigned_ak', ($event.target as HTMLSelectElement).value || null)"
            >
              <option v-for="ak in arbeitskreise" :key="ak.value" :value="ak.value">
                {{ ak.label }}
              </option>
            </select>
          </div>

          <!-- Sort -->
          <div>
            <label class="block text-xs font-medium text-gray-700 mb-1">Sortierung</label>
            <div class="flex gap-1">
              <select
                class="input text-sm flex-1"
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
            </div>
          </div>
        </div>

        <!-- Clear Filters -->
        <div class="mt-3 flex justify-end">
          <button
            v-if="hasActiveFilters"
            type="button"
            class="text-sm text-liga-600 hover:text-liga-700 flex items-center gap-1"
            @click="clearAllFilters"
          >
            <XMarkIcon class="h-4 w-4" />
            Filter zurücksetzen
          </button>
        </div>
      </div>

      <!-- Loading -->
      <div v-if="itemsStore.loading" class="mt-4 flex items-center justify-center py-8">
        <ArrowPathIcon class="h-6 w-6 animate-spin text-gray-400" />
      </div>

      <!-- Empty State -->
      <div v-else-if="itemsStore.items.length === 0" class="mt-4 py-8 text-center text-gray-500">
        Keine Nachrichten gefunden
      </div>

      <!-- Scrollable News List -->
      <div v-else class="mt-4 max-h-[600px] overflow-y-auto">
        <ul class="divide-y divide-gray-100">
          <li v-for="item in itemsStore.items" :key="item.id">
            <RouterLink
              :to="`/items/${item.id}`"
              class="block py-3 hover:bg-gray-50 -mx-4 px-4 transition-colors"
            >
              <div class="flex items-start gap-3">
                <PriorityBadge :priority="item.priority" class="mt-0.5 flex-shrink-0" />
                <div class="min-w-0 flex-1">
                  <p
                    class="truncate text-sm font-medium"
                    :class="item.is_read ? 'text-gray-500' : 'text-gray-900'"
                  >
                    {{ item.title }}
                  </p>
                  <p class="mt-1 flex items-center gap-1 truncate text-xs text-gray-500">
                    <SourceIcon v-if="item.source" :connector-type="item.source.connector_type" size="sm" />
                    {{ item.source?.name ?? 'Unbekannte Quelle' }} &middot;
                    {{ formatTime(item.published_at) }}
                    <span v-if="item.metadata?.llm_analysis?.assigned_ak" class="ml-2 rounded bg-gray-100 px-1.5 py-0.5 text-xs">
                      {{ item.metadata.llm_analysis.assigned_ak }}
                    </span>
                  </p>
                </div>
              </div>
            </RouterLink>
          </li>
        </ul>
      </div>

      <!-- Pagination -->
      <div v-if="itemsStore.total > pageSize" class="mt-4 flex items-center justify-between border-t pt-4">
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
    <div class="flex flex-wrap gap-3">
      <button
        type="button"
        class="btn btn-primary"
        :disabled="sourcesStore.fetching !== null"
        @click="sourcesStore.triggerFetchAll()"
      >
        <ArrowPathIcon
          class="mr-2 h-4 w-4"
          :class="{ 'animate-spin': sourcesStore.fetching !== null }"
        />
        Alle Quellen abrufen
      </button>
      <RouterLink to="/sources" class="btn btn-secondary">
        Quellen verwalten
      </RouterLink>
      <RouterLink to="/rules" class="btn btn-secondary">
        Regeln konfigurieren
      </RouterLink>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch, computed } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { useItemsStore, useSourcesStore } from '@/stores'
import {
  ArrowPathIcon,
  CheckIcon,
  XMarkIcon,
  MagnifyingGlassIcon,
  ChevronUpDownIcon
} from '@heroicons/vue/24/outline'
import PriorityBadge from '@/components/PriorityBadge.vue'
import SourceIcon from '@/components/SourceIcon.vue'
import { formatDistanceToNow } from 'date-fns'
import { de } from 'date-fns/locale'
import type { Priority } from '@/types'
import { useKeyboardShortcuts } from '@/composables/useKeyboardShortcuts'

const router = useRouter()
const itemsStore = useItemsStore()
const sourcesStore = useSourcesStore()

const searchQuery = ref('')
const page = ref(1)
const pageSize = 50
const selectedItems = ref<number[]>([])
const focusedIndex = ref(-1)

const focusedItem = computed(() =>
  focusedIndex.value >= 0 ? itemsStore.items[focusedIndex.value] : null
)

useKeyboardShortcuts([
  {
    key: 'j',
    description: 'Nächster Eintrag',
    action: () => {
      if (focusedIndex.value < itemsStore.items.length - 1) {
        focusedIndex.value++
      }
    }
  },
  {
    key: 'k',
    description: 'Vorheriger Eintrag',
    action: () => {
      if (focusedIndex.value > 0) {
        focusedIndex.value--
      } else if (focusedIndex.value === -1 && itemsStore.items.length > 0) {
        focusedIndex.value = 0
      }
    }
  },
  {
    key: 'Enter',
    description: 'Eintrag öffnen',
    action: () => {
      if (focusedItem.value) {
        router.push(`/items/${focusedItem.value.id}`)
      }
    }
  },
  {
    key: 'm',
    description: 'Als gelesen markieren',
    action: () => {
      if (focusedItem.value && !focusedItem.value.is_read) {
        itemsStore.markAsRead(focusedItem.value.id)
      }
    }
  },
  {
    key: 'x',
    description: 'Auswählen/Abwählen',
    action: () => {
      if (focusedItem.value) {
        const id = focusedItem.value.id
        if (selectedItems.value.includes(id)) {
          selectedItems.value = selectedItems.value.filter(i => i !== id)
        } else {
          selectedItems.value.push(id)
        }
      }
    }
  }
])

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

const hasActiveFilters = computed(() => {
  return (
    itemsStore.filters.priority ||
    itemsStore.filters.source_id ||
    itemsStore.filters.connector_type ||
    itemsStore.filters.assigned_ak ||
    itemsStore.filters.search ||
    itemsStore.filters.is_read !== null
  )
})

const formatTime = (date: string | null) => {
  if (!date) return ''
  return formatDistanceToNow(new Date(date), { addSuffix: true, locale: de })
}

const loadItems = () => {
  itemsStore.fetchItems({
    page: page.value,
    page_size: pageSize
  })
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

const toggleSelectAll = () => {
  if (selectedItems.value.length === itemsStore.items.length) {
    selectedItems.value = []
  } else {
    selectedItems.value = itemsStore.items.map((i) => i.id)
  }
}

const markSelectedAsRead = async () => {
  if (selectedItems.value.length > 0) {
    await itemsStore.bulkMarkAsRead(selectedItems.value)
    selectedItems.value = []
  }
}

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

onMounted(async () => {
  await Promise.all([loadItems(), sourcesStore.fetchSources()])
})
</script>

<template>
  <div class="space-y-3">
    <div class="flex items-center justify-between">
      <h1 class="text-xl font-bold text-gray-900">Nachrichten</h1>
      <div class="flex gap-2">
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
    </div>

    <!-- Bulk Actions -->
    <div v-if="selectedItems.length > 0" class="rounded-lg border border-blue-300 bg-blue-100 px-4 py-2 flex items-center gap-4">
      <span class="text-sm font-medium text-black">
        {{ selectedItems.length }} ausgewählt
      </span>
      <button
        type="button"
        class="btn btn-secondary text-sm"
        @click="markSelectedAsRead"
      >
        <CheckIcon class="mr-1 h-4 w-4" />
        Als gelesen markieren
      </button>
      <button
        type="button"
        class="text-sm text-gray-500 hover:text-gray-700"
        @click="selectedItems = []"
      >
        <XMarkIcon class="h-4 w-4" />
      </button>
    </div>

    <!-- Items List -->
    <div class="rounded-lg border border-blue-300 overflow-hidden">
      <!-- Filter Bar -->
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

        <!-- Status -->
        <select
          class="input text-sm w-auto"
          :value="itemsStore.filters.is_read ?? ''"
          @change="itemsStore.setFilter('is_read', ($event.target as HTMLSelectElement).value === '' ? null : ($event.target as HTMLSelectElement).value === 'true')"
        >
          <option value="">Alle Status</option>
          <option value="false">Ungelesen</option>
          <option value="true">Gelesen</option>
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

        <!-- Select All -->
        <label class="flex items-center gap-2 ml-2">
          <input
            type="checkbox"
            class="rounded border-gray-300"
            :checked="selectedItems.length === itemsStore.items.length && itemsStore.items.length > 0"
            @change="toggleSelectAll"
          />
          <span class="text-sm text-white">Alle</span>
        </label>

        <!-- Result count -->
        <span class="ml-auto text-sm font-semibold text-black">{{ itemsStore.total }} Ergebnisse</span>
      </div>

      <!-- Loading -->
      <div v-if="itemsStore.loading" class="flex items-center justify-center py-6 bg-blue-100">
        <ArrowPathIcon class="h-6 w-6 animate-spin text-blue-500" />
      </div>

      <!-- Empty State -->
      <div v-else-if="itemsStore.items.length === 0" class="py-6 text-center text-gray-500 bg-blue-100">
        Keine Nachrichten gefunden
      </div>

      <!-- Scrollable News List -->
      <div v-else class="max-h-[calc(100vh-280px)] min-h-[200px] overflow-y-auto">
        <ul>
          <li v-for="(item, index) in itemsStore.items" :key="item.id">
            <div
              class="flex items-center py-2 px-4 transition-colors hover:bg-yellow-100 cursor-pointer"
              :class="[
                index % 2 === 1 ? 'bg-blue-200' : 'bg-blue-100',
                focusedIndex === index ? 'ring-2 ring-inset ring-blue-500' : ''
              ]"
              @click="focusedIndex = index"
            >
              <input
                type="checkbox"
                class="rounded border-gray-300 mr-3"
                :checked="selectedItems.includes(item.id)"
                @click.stop
                @change="selectedItems.includes(item.id) ? selectedItems = selectedItems.filter(i => i !== item.id) : selectedItems.push(item.id)"
              />
              <PriorityBadge :priority="item.priority" class="flex-shrink-0 mr-2" />
              <RouterLink
                :to="`/items/${item.id}`"
                class="min-w-0 flex-1 truncate text-sm"
                :class="item.is_read ? 'text-gray-500' : 'font-medium text-gray-900'"
                @click.stop
              >
                {{ item.title }}
              </RouterLink>
              <span class="flex items-center gap-2 text-xs text-black flex-shrink-0 ml-4">
                <SourceIcon v-if="item.channel" :connector-type="item.channel.connector_type" size="sm" />
                <span class="hidden sm:inline">{{ item.source?.name ?? 'Unbekannt' }}</span>
                <span class="hidden md:inline">&middot;</span>
                <span class="hidden md:inline">{{ formatTime(item.published_at) }}</span>
                <span v-if="item.metadata?.llm_analysis?.assigned_ak" class="rounded bg-blue-300 px-1 text-xs font-medium text-black">
                  {{ item.metadata.llm_analysis.assigned_ak }}
                </span>
              </span>
            </div>
          </li>
        </ul>
      </div>

      <!-- Pagination -->
      <div v-if="itemsStore.total > pageSize" class="flex items-center justify-between border-t border-blue-300 bg-blue-100 px-4 py-2">
        <p class="text-sm text-black">
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
  </div>
</template>

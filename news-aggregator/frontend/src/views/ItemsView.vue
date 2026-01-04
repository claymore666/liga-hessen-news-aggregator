<script setup lang="ts">
import { onMounted, ref, watch, computed } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { useItemsStore, useSourcesStore } from '@/stores'
import {
  ArrowPathIcon,
  FunnelIcon,
  CheckIcon,
  XMarkIcon
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

const showFilters = ref(false)
const page = ref(0)
const pageSize = 20
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

const priorities: { value: Priority | null; label: string }[] = [
  { value: null, label: 'Alle Prioritäten' },
  { value: 'critical', label: 'Kritisch' },
  { value: 'high', label: 'Hoch' },
  { value: 'medium', label: 'Mittel' },
  { value: 'low', label: 'Niedrig' }
]

const formatTime = (date: string | null) => {
  if (!date) return ''
  return formatDistanceToNow(new Date(date), { addSuffix: true, locale: de })
}

const loadItems = () => {
  itemsStore.fetchItems({
    skip: page.value * pageSize,
    limit: pageSize
  })
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
  () => itemsStore.filters,
  () => {
    page.value = 0
    loadItems()
  },
  { deep: true }
)

onMounted(async () => {
  await Promise.all([loadItems(), sourcesStore.fetchSources()])
})
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-bold text-gray-900">Nachrichten</h1>
        <p class="text-sm text-gray-500">
          {{ itemsStore.total }} Nachrichten, {{ itemsStore.unreadCount }} ungelesen
        </p>
      </div>
      <div class="flex gap-2">
        <button
          type="button"
          class="btn btn-secondary"
          @click="showFilters = !showFilters"
        >
          <FunnelIcon class="mr-2 h-4 w-4" />
          Filter
        </button>
        <button
          type="button"
          class="btn btn-primary"
          :disabled="itemsStore.loading"
          @click="loadItems"
        >
          <ArrowPathIcon
            class="mr-2 h-4 w-4"
            :class="{ 'animate-spin': itemsStore.loading }"
          />
          Aktualisieren
        </button>
      </div>
    </div>

    <!-- Filters -->
    <div v-if="showFilters" class="card">
      <div class="grid gap-4 sm:grid-cols-3">
        <div>
          <label class="block text-sm font-medium text-gray-700">Priorität</label>
          <select
            class="input mt-1"
            :value="itemsStore.filters.priority"
            @change="itemsStore.setFilter('priority', ($event.target as HTMLSelectElement).value || null)"
          >
            <option v-for="p in priorities" :key="p.label" :value="p.value ?? ''">
              {{ p.label }}
            </option>
          </select>
        </div>
        <div>
          <label class="block text-sm font-medium text-gray-700">Quelle</label>
          <select
            class="input mt-1"
            :value="itemsStore.filters.source_id"
            @change="itemsStore.setFilter('source_id', parseInt(($event.target as HTMLSelectElement).value) || null)"
          >
            <option value="">Alle Quellen</option>
            <option v-for="s in sourcesStore.sources" :key="s.id" :value="s.id">
              {{ s.name }}
            </option>
          </select>
        </div>
        <div>
          <label class="block text-sm font-medium text-gray-700">Status</label>
          <select
            class="input mt-1"
            :value="itemsStore.filters.is_read"
            @change="itemsStore.setFilter('is_read', ($event.target as HTMLSelectElement).value === '' ? null : ($event.target as HTMLSelectElement).value === 'true')"
          >
            <option value="">Alle</option>
            <option value="false">Ungelesen</option>
            <option value="true">Gelesen</option>
          </select>
        </div>
      </div>
      <div class="mt-4 flex justify-end">
        <button
          type="button"
          class="text-sm text-liga-600 hover:text-liga-700"
          @click="itemsStore.clearFilters()"
        >
          Filter zurücksetzen
        </button>
      </div>
    </div>

    <!-- Bulk Actions -->
    <div v-if="selectedItems.length > 0" class="card flex items-center gap-4">
      <span class="text-sm text-gray-600">
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
    <div class="card p-0">
      <div v-if="itemsStore.loading" class="flex items-center justify-center py-12">
        <ArrowPathIcon class="h-8 w-8 animate-spin text-gray-400" />
      </div>

      <div v-else-if="itemsStore.items.length === 0" class="py-12 text-center text-gray-500">
        Keine Nachrichten gefunden
      </div>

      <table v-else class="w-full">
        <thead class="border-b border-gray-200 bg-gray-50">
          <tr>
            <th class="px-4 py-3 text-left">
              <input
                type="checkbox"
                class="rounded border-gray-300"
                :checked="selectedItems.length === itemsStore.items.length"
                @change="toggleSelectAll"
              />
            </th>
            <th class="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">
              Priorität
            </th>
            <th class="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">
              Titel
            </th>
            <th class="hidden px-4 py-3 text-left text-xs font-medium uppercase text-gray-500 lg:table-cell">
              Quelle
            </th>
            <th class="hidden px-4 py-3 text-left text-xs font-medium uppercase text-gray-500 sm:table-cell">
              Zeit
            </th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <tr
            v-for="(item, index) in itemsStore.items"
            :key="item.id"
            class="hover:bg-gray-50 cursor-pointer"
            :class="{
              'bg-gray-50': item.is_read,
              'ring-2 ring-inset ring-liga-500': focusedIndex === index
            }"
            @click="focusedIndex = index"
          >
            <td class="px-4 py-3">
              <input
                type="checkbox"
                class="rounded border-gray-300"
                :checked="selectedItems.includes(item.id)"
                @change="selectedItems.includes(item.id) ? selectedItems = selectedItems.filter(i => i !== item.id) : selectedItems.push(item.id)"
              />
            </td>
            <td class="px-4 py-3">
              <PriorityBadge :priority="item.priority" />
            </td>
            <td class="max-w-md px-4 py-3">
              <RouterLink
                :to="`/items/${item.id}`"
                class="block truncate text-sm hover:text-liga-600"
                :class="item.is_read ? 'text-gray-500' : 'font-medium text-gray-900'"
              >
                {{ item.title }}
              </RouterLink>
            </td>
            <td class="hidden px-4 py-3 text-sm text-gray-500 lg:table-cell">
              <span class="flex items-center gap-2">
                <SourceIcon v-if="item.source" :connector-type="item.source.connector_type" size="sm" />
                {{ item.source?.name ?? 'Unbekannt' }}
              </span>
            </td>
            <td class="hidden whitespace-nowrap px-4 py-3 text-sm text-gray-500 sm:table-cell">
              {{ formatTime(item.published_at) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Pagination -->
    <div class="flex items-center justify-between">
      <p class="text-sm text-gray-500">
        Zeige {{ page * pageSize + 1 }} - {{ Math.min((page + 1) * pageSize, itemsStore.total) }}
        von {{ itemsStore.total }}
      </p>
      <div class="flex gap-2">
        <button
          type="button"
          class="btn btn-secondary"
          :disabled="page === 0"
          @click="page--; loadItems()"
        >
          Zurück
        </button>
        <button
          type="button"
          class="btn btn-secondary"
          :disabled="(page + 1) * pageSize >= itemsStore.total"
          @click="page++; loadItems()"
        >
          Weiter
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, computed } from 'vue'
import { RouterLink } from 'vue-router'
import { useItemsStore, useSourcesStore, useStatsStore } from '@/stores'
import {
  NewspaperIcon,
  RssIcon,
  ExclamationTriangleIcon,
  ClockIcon,
  ArrowPathIcon
} from '@heroicons/vue/24/outline'
import PriorityBadge from '@/components/PriorityBadge.vue'
import SourceIcon from '@/components/SourceIcon.vue'
import { formatDistanceToNow } from 'date-fns'
import { de } from 'date-fns/locale'

const itemsStore = useItemsStore()
const sourcesStore = useSourcesStore()
const statsStore = useStatsStore()

const recentItems = computed(() => itemsStore.items.slice(0, 10))

const formatTime = (date: string | null) => {
  if (!date) return 'Nie'
  return formatDistanceToNow(new Date(date), { addSuffix: true, locale: de })
}

onMounted(async () => {
  await Promise.all([
    statsStore.fetchStats(),
    itemsStore.fetchItems({ limit: 10 }),
    sourcesStore.fetchSources()
  ])
})
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
        <div class="rounded-lg bg-red-50 p-4 text-center">
          <p class="text-3xl font-bold text-red-600">
            {{ statsStore.stats?.items_by_priority?.critical ?? 0 }}
          </p>
          <p class="text-sm text-red-700">Kritisch</p>
        </div>
        <div class="rounded-lg bg-orange-50 p-4 text-center">
          <p class="text-3xl font-bold text-orange-600">
            {{ statsStore.stats?.items_by_priority?.high ?? 0 }}
          </p>
          <p class="text-sm text-orange-700">Hoch</p>
        </div>
        <div class="rounded-lg bg-yellow-50 p-4 text-center">
          <p class="text-3xl font-bold text-yellow-600">
            {{ statsStore.stats?.items_by_priority?.medium ?? 0 }}
          </p>
          <p class="text-sm text-yellow-700">Mittel</p>
        </div>
        <div class="rounded-lg bg-green-50 p-4 text-center">
          <p class="text-3xl font-bold text-green-600">
            {{ statsStore.stats?.items_by_priority?.low ?? 0 }}
          </p>
          <p class="text-sm text-green-700">Niedrig</p>
        </div>
      </div>
    </div>

    <!-- Recent Items -->
    <div class="card">
      <div class="flex items-center justify-between">
        <h2 class="text-lg font-medium text-gray-900">Aktuelle Nachrichten</h2>
        <RouterLink to="/items" class="text-sm text-liga-600 hover:text-liga-700">
          Alle anzeigen
        </RouterLink>
      </div>

      <div v-if="itemsStore.loading" class="mt-4 flex items-center justify-center py-8">
        <ArrowPathIcon class="h-6 w-6 animate-spin text-gray-400" />
      </div>

      <div v-else-if="recentItems.length === 0" class="mt-4 py-8 text-center text-gray-500">
        Keine Nachrichten vorhanden
      </div>

      <ul v-else class="mt-4 divide-y divide-gray-100">
        <li v-for="item in recentItems" :key="item.id">
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
                </p>
              </div>
            </div>
          </RouterLink>
        </li>
      </ul>
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

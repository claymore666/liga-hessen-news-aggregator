<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import { useStatsStore, useUiStore } from '@/stores'
import {
  NewspaperIcon,
  RssIcon,
  ExclamationTriangleIcon,
  ClockIcon,
  ArrowPathIcon,
} from '@heroicons/vue/24/outline'
import { formatDistanceToNow } from 'date-fns'
import { de } from 'date-fns/locale'
import axios from 'axios'
import TopicWordCloud from '@/components/TopicWordCloud.vue'
import SourceDonutChart from '@/components/SourceDonutChart.vue'
import PriorityBadge from '@/components/PriorityBadge.vue'
import SourceIcon from '@/components/SourceIcon.vue'
import type { Priority } from '@/types'

const statsStore = useStatsStore()
const uiStore = useUiStore()

const timeRanges = [
  { days: 1, label: '1T' },
  { days: 3, label: '3T' },
  { days: 7, label: '1W' },
  { days: 30, label: '1M' },
  { days: 365, label: 'Alle' },
]

const formatTime = (date: string | null) => {
  if (!date) return 'Nie'
  return formatDistanceToNow(new Date(date), { addSuffix: true, locale: de })
}

// Items list
interface ItemEntry {
  id: number
  title: string
  priority: Priority
  is_read: boolean
  published_at: string
  source?: { name: string }
  channel?: { connector_type: string }
  metadata?: Record<string, any>
}

const items = ref<ItemEntry[]>([])
const itemsLoading = ref(false)
const totalItems = ref(0)

async function loadItems() {
  itemsLoading.value = true
  try {
    const { data } = await axios.get('/api/items', {
      params: {
        page: 1,
        page_size: 30,
        sort_by: 'priority',
        sort_order: 'desc',
        relevant_only: true,
        days: uiStore.periodDays,
      }
    })
    items.value = data.items
    totalItems.value = data.total
  } catch (e) {
    console.error('Failed to load items', e)
  } finally {
    itemsLoading.value = false
  }
}

function selectPeriod(days: number) {
  uiStore.setPeriodDays(days)
}

watch(() => uiStore.periodDays, () => {
  statsStore.fetchStats({ days: uiStore.periodDays })
  loadItems()
})

onMounted(() => {
  statsStore.fetchStats({ days: uiStore.periodDays })
  loadItems()
})
</script>

<template>
  <div class="space-y-3">
    <div class="flex items-center justify-between">
      <h1 class="text-xl font-bold text-gray-900">Übersicht</h1>
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
      <div class="rounded-lg py-1.5 px-3 bg-red-500 text-center">
        <span class="text-lg font-bold text-white">{{ statsStore.stats?.items_by_priority?.high ?? 0 }}</span>
        <span class="text-xs text-red-100 ml-1">Hoch</span>
      </div>
      <div class="rounded-lg py-1.5 px-3 bg-orange-500 text-center">
        <span class="text-lg font-bold text-white">{{ statsStore.stats?.items_by_priority?.medium ?? 0 }}</span>
        <span class="text-xs text-orange-100 ml-1">Mittel</span>
      </div>
      <div class="rounded-lg py-1.5 px-3 bg-yellow-400 text-center">
        <span class="text-lg font-bold text-gray-900">{{ statsStore.stats?.items_by_priority?.low ?? 0 }}</span>
        <span class="text-xs text-yellow-800 ml-1">Niedrig</span>
      </div>
      <div class="rounded-lg py-1.5 px-3 bg-blue-500 text-center">
        <span class="text-lg font-bold text-white">{{ statsStore.stats?.items_by_priority?.none ?? 0 }}</span>
        <span class="text-xs text-blue-100 ml-1">Keine</span>
      </div>
    </div>

    <!-- Time Period Buttons -->
    <div class="flex justify-center gap-1">
      <button
        v-for="r in timeRanges"
        :key="r.days"
        class="px-3 py-1.5 text-sm rounded-lg font-medium transition-colors"
        :class="uiStore.periodDays === r.days
          ? 'bg-blue-600 text-white'
          : 'bg-gray-100 text-gray-600 hover:bg-gray-200'"
        @click="selectPeriod(r.days)"
      >
        {{ r.label }}
      </button>
    </div>

    <!-- Charts -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <div class="bg-white rounded-xl shadow p-6">
        <TopicWordCloud :days="uiStore.periodDays" />
      </div>
      <div class="bg-white rounded-xl shadow p-6">
        <SourceDonutChart :days="uiStore.periodDays" />
      </div>
    </div>

    <!-- Recent Items -->
    <div class="rounded-lg border border-blue-300 overflow-hidden">
      <div class="bg-blue-400 px-4 py-2 border-b border-blue-500">
        <span class="text-sm font-semibold text-white">Neueste relevante Nachrichten</span>
      </div>

      <div v-if="itemsLoading" class="flex items-center justify-center py-6">
        <ArrowPathIcon class="h-6 w-6 animate-spin text-gray-400" />
      </div>

      <div v-else-if="items.length === 0" class="py-6 text-center text-gray-500">
        Keine Nachrichten gefunden
      </div>

      <div v-else class="max-h-[33vh] overflow-y-auto">
        <ul>
          <li v-for="(item, index) in items" :key="item.id">
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
                  <span class="max-w-20 truncate">{{ item.metadata?.source_domain ?? item.source?.name ?? '' }}</span>
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
    </div>
  </div>
</template>

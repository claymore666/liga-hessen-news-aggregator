<script setup lang="ts">
import type { TopicGroup } from '@/api'
import PriorityBadge from '@/components/PriorityBadge.vue'
import { formatDistanceToNow } from 'date-fns'
import { de } from 'date-fns/locale'

defineProps<{
  topics: TopicGroup[]
  ungroupedCount: number
  selectedId: number | null
}>()

const emit = defineEmits<{
  (e: 'select', id: number): void
}>()

const formatTime = (date: string | null) => {
  if (!date) return ''
  return formatDistanceToNow(new Date(date), { addSuffix: false, locale: de })
}
</script>

<template>
  <div class="overflow-y-auto flex-1">
    <div v-if="topics.length === 0" class="py-8 text-center text-gray-500 bg-blue-50">
      Keine Themengruppen gefunden
    </div>
    <div v-else>
      <div v-for="group in topics" :key="group.topic" class="mb-1">
        <!-- Topic header -->
        <div class="flex items-center gap-2 px-3 py-1.5 bg-gray-100 border-l-4 border-l-indigo-400 sticky top-0 z-10">
          <span class="font-semibold text-xs text-indigo-800 truncate">{{ group.topic }}</span>
          <span class="rounded bg-indigo-100 px-1.5 py-0.5 text-[10px] font-medium text-indigo-600 flex-shrink-0">
            {{ group.items.length }}
          </span>
        </div>
        <!-- Items in topic -->
        <ul>
          <li
            v-for="(item, idx) in group.items"
            :key="item.id"
            class="flex items-center py-1.5 px-3 pl-6 transition-colors cursor-pointer border-l-4"
            :class="[
              selectedId === item.id
                ? 'bg-blue-200 border-l-indigo-500'
                : idx % 2 === 1
                  ? 'bg-blue-100 border-l-transparent hover:bg-yellow-100 hover:border-l-yellow-400'
                  : 'bg-blue-50 border-l-transparent hover:bg-yellow-100 hover:border-l-yellow-400',
            ]"
            @click="emit('select', item.id)"
          >
            <PriorityBadge :priority="item.priority" size="sm" class="flex-shrink-0 mr-2" />
            <span class="min-w-0 flex-1 truncate text-xs font-medium text-gray-900">
              {{ item.title }}
            </span>
            <span class="flex items-center gap-1.5 text-[10px] text-gray-600 flex-shrink-0 ml-2">
              <span class="hidden lg:inline max-w-20 truncate">{{ item.source_name ?? '' }}</span>
              <span class="text-gray-400">{{ formatTime(item.published_at) }}</span>
            </span>
          </li>
        </ul>
      </div>

      <!-- Ungrouped count -->
      <div v-if="ungroupedCount > 0" class="px-3 py-2 bg-gray-50 border-l-4 border-l-gray-300 text-xs text-gray-500">
        Sonstiges: {{ ungroupedCount }} weitere Artikel ohne Themengruppe
      </div>
    </div>
  </div>
</template>

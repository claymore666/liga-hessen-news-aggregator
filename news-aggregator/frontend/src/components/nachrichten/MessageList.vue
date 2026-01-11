<script setup lang="ts">
import type { Item } from '@/types'
import PriorityBadge from '@/components/PriorityBadge.vue'
import SourceIcon from '@/components/SourceIcon.vue'
import { CheckCircleIcon } from '@heroicons/vue/24/solid'
import { formatDistanceToNow } from 'date-fns'
import { de } from 'date-fns/locale'

const props = defineProps<{
  items: Item[]
  selectedId: number | null
  selectedIds: number[]
  focusedIndex: number
}>()

const emit = defineEmits<{
  (e: 'select', id: number): void
  (e: 'toggle-check', id: number): void
  (e: 'focus', index: number): void
}>()

const formatTime = (date: string | null) => {
  if (!date) return ''
  return formatDistanceToNow(new Date(date), { addSuffix: false, locale: de })
}
</script>

<template>
  <div class="overflow-y-auto flex-1">
    <div v-if="items.length === 0" class="py-8 text-center text-gray-500 bg-blue-50">
      Keine Nachrichten gefunden
    </div>
    <ul v-else>
      <li v-for="(item, index) in items" :key="item.id">
        <div
          class="flex items-center py-1.5 px-3 transition-colors cursor-pointer border-l-4"
          :class="[
            selectedId === item.id
              ? 'bg-blue-200 border-l-blue-600'
              : index % 2 === 1
                ? 'bg-blue-100 border-l-transparent hover:bg-yellow-100 hover:border-l-yellow-400'
                : 'bg-blue-50 border-l-transparent hover:bg-yellow-100 hover:border-l-yellow-400',
            focusedIndex === index ? 'ring-1 ring-inset ring-blue-500' : ''
          ]"
          @click="emit('select', item.id); emit('focus', index)"
        >
          <!-- Checkbox -->
          <input
            type="checkbox"
            class="rounded border-gray-300 h-3.5 w-3.5 mr-2 flex-shrink-0"
            :checked="selectedIds.includes(item.id)"
            @click.stop
            @change="emit('toggle-check', item.id)"
          />

          <!-- Reviewed indicator -->
          <CheckCircleIcon
            v-if="item.is_manually_reviewed"
            class="h-3.5 w-3.5 text-green-500 flex-shrink-0 mr-1"
            title="Manuell überprüft"
          />

          <!-- Priority badge -->
          <PriorityBadge :priority="item.priority" size="sm" class="flex-shrink-0 mr-2" />

          <!-- Title -->
          <span
            class="min-w-0 flex-1 truncate text-xs"
            :class="item.is_read ? 'text-gray-500' : 'font-medium text-gray-900'"
          >
            {{ item.title }}
          </span>

          <!-- Metadata -->
          <span class="flex items-center gap-1.5 text-[10px] text-gray-600 flex-shrink-0 ml-2">
            <SourceIcon v-if="item.channel" :connector-type="item.channel.connector_type" size="xs" />
            <span class="hidden lg:inline max-w-20 truncate">{{ item.source?.name ?? '' }}</span>
            <span class="text-gray-400">{{ formatTime(item.published_at) }}</span>
            <span
              v-if="(item.assigned_ak || item.metadata?.llm_analysis?.assigned_ak) && item.priority !== 'none'"
              class="rounded bg-purple-200 px-1 py-0.5 text-[10px] font-medium text-purple-800"
            >
              {{ item.assigned_ak || item.metadata?.llm_analysis?.assigned_ak }}
            </span>
          </span>
        </div>
      </li>
    </ul>
  </div>
</template>

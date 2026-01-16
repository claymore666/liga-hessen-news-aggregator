<script setup lang="ts">
import { ref } from 'vue'
import type { Item } from '@/types'
import PriorityBadge from '@/components/PriorityBadge.vue'
import SourceIcon from '@/components/SourceIcon.vue'
import { CheckCircleIcon, ChevronDownIcon, ChevronRightIcon } from '@heroicons/vue/24/solid'
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

// Track which items have expanded duplicates
const expandedItems = ref<Set<number>>(new Set())

const toggleExpand = (itemId: number) => {
  if (expandedItems.value.has(itemId)) {
    expandedItems.value.delete(itemId)
  } else {
    expandedItems.value.add(itemId)
  }
}

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
      <li v-for="(item, index) in items" :key="`${item.id}-${item.is_read}`">
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
          <!-- Expand/collapse duplicates toggle -->
          <button
            v-if="item.duplicates?.length"
            class="mr-1 flex-shrink-0 text-gray-400 hover:text-gray-600"
            @click.stop="toggleExpand(item.id)"
            :title="`${item.duplicates.length} weitere Artikel zum Thema`"
          >
            <ChevronDownIcon v-if="expandedItems.has(item.id)" class="h-3.5 w-3.5" />
            <ChevronRightIcon v-else class="h-3.5 w-3.5" />
          </button>
          <span v-else class="w-4 mr-1 flex-shrink-0" />

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
            :class="item.is_read ? 'text-gray-500 font-normal' : 'font-bold text-gray-900'"
          >
            {{ item.title }}
          </span>

          <!-- Duplicates count badge -->
          <span
            v-if="item.duplicates?.length"
            class="rounded bg-orange-100 px-1 py-0.5 text-[10px] font-medium text-orange-700 flex-shrink-0 mx-1"
            :title="`${item.duplicates.length} weitere Artikel zum Thema`"
          >
            +{{ item.duplicates.length }}
          </span>

          <!-- Metadata -->
          <span class="flex items-center gap-1.5 text-[10px] text-gray-600 flex-shrink-0 ml-2">
            <SourceIcon v-if="item.channel" :connector-type="item.channel.connector_type" size="xs" />
            <span class="hidden lg:inline max-w-20 truncate">{{ item.source?.name ?? '' }}</span>
            <span class="text-gray-400">{{ formatTime(item.published_at) }}</span>
            <span
              v-for="ak in (item.assigned_aks?.length ? item.assigned_aks : (item.metadata?.llm_analysis?.assigned_aks || (item.assigned_ak ? [item.assigned_ak] : [])))"
              v-if="item.priority !== 'none'"
              :key="ak"
              class="rounded bg-purple-200 px-1 py-0.5 text-[10px] font-medium text-purple-800"
            >
              {{ ak }}
            </span>
          </span>
        </div>

        <!-- Expanded duplicates list -->
        <ul v-if="item.duplicates?.length && expandedItems.has(item.id)" class="ml-6 border-l-2 border-orange-200">
          <li
            v-for="dup in item.duplicates"
            :key="dup.id"
            class="flex items-center py-1 px-3 text-xs text-gray-600 bg-orange-50 hover:bg-orange-100 cursor-pointer"
            @click="$emit('select', dup.id)"
          >
            <span class="w-4 mr-1 flex-shrink-0 text-orange-300">└</span>
            <PriorityBadge :priority="dup.priority" size="sm" class="flex-shrink-0 mr-2" />
            <span class="truncate flex-1">{{ dup.title }}</span>
            <span class="text-[10px] text-gray-400 ml-2 flex-shrink-0">
              {{ dup.source?.name ?? '' }}
            </span>
          </li>
        </ul>
      </li>
    </ul>
  </div>
</template>

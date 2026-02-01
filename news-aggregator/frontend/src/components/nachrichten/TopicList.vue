<script setup lang="ts">
import { ref } from 'vue'
import type { TopicGroup, TopicItemBrief } from '@/api'
import type { Priority } from '@/types'
import PriorityBadge from '@/components/PriorityBadge.vue'
import { ChevronDownIcon, ChevronRightIcon } from '@heroicons/vue/24/solid'
import { formatDistanceToNow } from 'date-fns'
import { de } from 'date-fns/locale'

defineProps<{
  topics: TopicGroup[]
  ungroupedItems: TopicItemBrief[]
  selectedId: number | null
}>()

const emit = defineEmits<{
  (e: 'select', id: number): void
}>()

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
    <div v-if="topics.length === 0 && ungroupedItems.length === 0" class="py-8 text-center text-gray-500 bg-blue-50">
      Keine Nachrichten gefunden
    </div>
    <div v-else>
      <!-- Topic groups -->
      <div v-for="group in topics" :key="group.topic" class="mb-0.5">
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
            :data-item-id="item.id"
          >
            <div
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

              <PriorityBadge :priority="(item.priority as Priority)" size="sm" class="flex-shrink-0 mr-2" />
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

              <span class="flex items-center gap-1.5 text-[10px] text-gray-600 flex-shrink-0 ml-2">
                <span class="hidden lg:inline max-w-20 truncate">{{ item.source_domain ?? item.source_name ?? '' }}</span>
                <span class="text-gray-400">{{ formatTime(item.published_at) }}</span>
                <span
                  v-for="ak in item.assigned_aks"
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
                <PriorityBadge :priority="(dup.priority as Priority)" size="sm" class="flex-shrink-0 mr-2" />
                <span class="truncate flex-1">{{ dup.title }}</span>
                <span class="text-[10px] text-gray-400 ml-2 flex-shrink-0">
                  {{ dup.source?.name ?? '' }}
                </span>
              </li>
            </ul>
          </li>
        </ul>
      </div>

      <!-- Sonstiges group -->
      <div v-if="ungroupedItems.length > 0" class="mb-0.5">
        <div class="flex items-center gap-2 px-3 py-1.5 bg-gray-100 border-l-4 border-l-gray-400 sticky top-0 z-10">
          <span class="font-semibold text-xs text-gray-600 truncate">Sonstiges</span>
          <span class="rounded bg-gray-200 px-1.5 py-0.5 text-[10px] font-medium text-gray-500 flex-shrink-0">
            {{ ungroupedItems.length }}
          </span>
        </div>
        <ul>
          <li
            v-for="(item, idx) in ungroupedItems"
            :key="item.id"
            :data-item-id="item.id"
          >
            <div
              class="flex items-center py-1.5 px-3 pl-6 transition-colors cursor-pointer border-l-4"
              :class="[
                selectedId === item.id
                  ? 'bg-blue-200 border-l-gray-400'
                  : idx % 2 === 1
                    ? 'bg-blue-100 border-l-transparent hover:bg-yellow-100 hover:border-l-yellow-400'
                    : 'bg-blue-50 border-l-transparent hover:bg-yellow-100 hover:border-l-yellow-400',
              ]"
              @click="emit('select', item.id)"
            >
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

              <PriorityBadge :priority="(item.priority as Priority)" size="sm" class="flex-shrink-0 mr-2" />
              <span
                class="min-w-0 flex-1 truncate text-xs"
                :class="item.is_read ? 'text-gray-500 font-normal' : 'font-bold text-gray-900'"
              >
                {{ item.title }}
              </span>

              <span
                v-if="item.duplicates?.length"
                class="rounded bg-orange-100 px-1 py-0.5 text-[10px] font-medium text-orange-700 flex-shrink-0 mx-1"
                :title="`${item.duplicates.length} weitere Artikel zum Thema`"
              >
                +{{ item.duplicates.length }}
              </span>

              <span class="flex items-center gap-1.5 text-[10px] text-gray-600 flex-shrink-0 ml-2">
                <span class="hidden lg:inline max-w-20 truncate">{{ item.source_domain ?? item.source_name ?? '' }}</span>
                <span class="text-gray-400">{{ formatTime(item.published_at) }}</span>
                <span
                  v-for="ak in item.assigned_aks"
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
                <PriorityBadge :priority="(dup.priority as Priority)" size="sm" class="flex-shrink-0 mr-2" />
                <span class="truncate flex-1">{{ dup.title }}</span>
                <span class="text-[10px] text-gray-400 ml-2 flex-shrink-0">
                  {{ dup.source?.name ?? '' }}
                </span>
              </li>
            </ul>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

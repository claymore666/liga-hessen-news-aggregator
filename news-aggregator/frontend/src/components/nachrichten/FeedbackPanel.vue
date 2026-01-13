<script setup lang="ts">
import { computed } from 'vue'
import type { Item, Priority } from '@/types'
import {
  StarIcon,
  CheckIcon,
  EnvelopeOpenIcon,
  ArchiveBoxIcon
} from '@heroicons/vue/24/outline'
import { StarIcon as StarIconSolid } from '@heroicons/vue/24/solid'

const props = defineProps<{
  item: Item
}>()

const emit = defineEmits<{
  (e: 'update:priority', priority: Priority): void
  (e: 'update:aks', aks: string[]): void
  (e: 'toggle:read'): void
  (e: 'archive'): void
}>()

const priorities: { value: Priority; label: string; shortcut: string }[] = [
  { value: 'high', label: 'Hoch', shortcut: '1' },
  { value: 'medium', label: 'Mittel', shortcut: '2' },
  { value: 'low', label: 'Niedrig', shortcut: '3' },
  { value: 'none', label: 'Keine', shortcut: '4' }
]

const arbeitskreise = [
  { value: 'AK1', label: 'AK1' },
  { value: 'AK2', label: 'AK2' },
  { value: 'AK3', label: 'AK3' },
  { value: 'AK4', label: 'AK4' },
  { value: 'AK5', label: 'AK5' },
  { value: 'QAG', label: 'QAG' }
]

const isRelevant = computed(() => props.item.priority !== 'none')

const setPriority = (priority: Priority) => {
  if (props.item.priority !== priority) {
    emit('update:priority', priority)
  }
}

const setAk = (ak: string) => {
  const current = props.item.assigned_aks || []
  const newAks = current.includes(ak)
    ? current.filter(a => a !== ak)  // Remove if present
    : [...current, ak]               // Add if not present
  emit('update:aks', newAks)
}

const isAkSelected = (ak: string) => {
  return (props.item.assigned_aks || []).includes(ak)
}

const toggleRelevance = () => {
  if (isRelevant.value) {
    emit('update:priority', 'none')
  } else {
    emit('update:priority', 'low')
  }
}
</script>

<template>
  <div class="rounded-lg border border-blue-300 bg-blue-50 p-3 space-y-3">
    <!-- Priority -->
    <div class="flex items-center gap-2">
      <span class="text-xs font-medium text-gray-600 w-16">Priorität:</span>
      <div class="flex gap-1">
        <button
          v-for="p in priorities"
          :key="p.value"
          type="button"
          class="px-2 py-1 text-xs font-medium rounded transition-all"
          :class="[
            item.priority === p.value
              ? p.value === 'high' ? 'bg-red-600 text-white ring-2 ring-red-300'
                : p.value === 'medium' ? 'bg-orange-500 text-white ring-2 ring-orange-300'
                : p.value === 'low' ? 'bg-yellow-500 text-white ring-2 ring-yellow-300'
                : 'bg-blue-600 text-white ring-2 ring-blue-300'
              : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
          ]"
          :title="`${p.label} (${p.shortcut})`"
          @click="setPriority(p.value)"
        >
          {{ p.label }}
        </button>
      </div>
    </div>

    <!-- Arbeitskreis -->
    <div class="flex items-center gap-2">
      <span class="text-xs font-medium text-gray-600 w-16">AK:</span>
      <div class="flex gap-1 flex-wrap">
        <button
          v-for="ak in arbeitskreise"
          :key="ak.value"
          type="button"
          class="px-2 py-1 text-xs font-medium rounded transition-all"
          :class="[
            isAkSelected(ak.value)
              ? 'bg-purple-600 text-white ring-2 ring-purple-300'
              : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
          ]"
          @click="setAk(ak.value)"
        >
          {{ ak.label }}
        </button>
      </div>
    </div>

    <!-- Relevance and Actions -->
    <div class="flex items-center gap-4">
      <div class="flex items-center gap-2">
        <span class="text-xs font-medium text-gray-600 w-16">Relevant:</span>
        <div class="flex gap-1">
          <button
            type="button"
            class="px-2 py-1 text-xs font-medium rounded transition-all"
            :class="[
              isRelevant
                ? 'bg-blue-600 text-white ring-2 ring-blue-300'
                : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
            ]"
            @click="toggleRelevance"
          >
            Ja
          </button>
          <button
            type="button"
            class="px-2 py-1 text-xs font-medium rounded transition-all"
            :class="[
              !isRelevant
                ? 'bg-gray-600 text-white ring-2 ring-gray-400'
                : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
            ]"
            @click="toggleRelevance"
          >
            Nein
          </button>
        </div>
      </div>

      <div class="flex-1" />

      <!-- Review status indicator -->
      <div v-if="item.is_manually_reviewed" class="flex items-center gap-1 text-xs text-green-600" title="Manuell geprüft">
        <CheckIcon class="h-4 w-4" />
        <span class="hidden xl:inline">Geprüft</span>
      </div>

      <!-- Action buttons -->
      <div class="flex items-center gap-2">
        <button
          type="button"
          class="p-1.5 rounded text-gray-500 hover:text-blue-600 hover:bg-blue-100 transition-colors"
          :title="item.is_read ? 'Als ungelesen markieren' : 'Als gelesen markieren'"
          @click="emit('toggle:read')"
        >
          <CheckIcon v-if="!item.is_read" class="h-5 w-5" />
          <EnvelopeOpenIcon v-else class="h-5 w-5" />
        </button>
        <button
          type="button"
          class="p-1.5 rounded transition-colors"
          :class="item.is_archived ? 'text-orange-600 bg-orange-100 hover:bg-orange-200' : 'text-gray-500 hover:text-red-600 hover:bg-red-100'"
          :title="item.is_archived ? 'Wiederherstellen' : 'Archivieren'"
          @click="emit('archive')"
        >
          <ArchiveBoxIcon class="h-5 w-5" />
        </button>
      </div>
    </div>
  </div>
</template>

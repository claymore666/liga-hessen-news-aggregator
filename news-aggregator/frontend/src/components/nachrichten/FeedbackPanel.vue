<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Item, Priority, ItemEvent } from '@/types'
import {
  StarIcon,
  CheckIcon,
  EnvelopeOpenIcon,
  ArchiveBoxIcon,
  ClockIcon,
  XMarkIcon
} from '@heroicons/vue/24/outline'
import { StarIcon as StarIconSolid } from '@heroicons/vue/24/solid'
import { itemsApi } from '@/api'

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

// History popup
const showHistory = ref(false)
const historyEvents = ref<ItemEvent[]>([])
const historyLoading = ref(false)

const loadHistory = async () => {
  historyLoading.value = true
  try {
    const response = await itemsApi.getHistory(props.item.id)
    historyEvents.value = response.data
    showHistory.value = true
  } catch (e) {
    console.error('Failed to load history:', e)
  } finally {
    historyLoading.value = false
  }
}

const eventTypeLabels: Record<string, string> = {
  pre_audit_trail: 'Vor Audit-Trail',
  created: 'Erstellt',
  classifier_processed: 'Klassifiziert',
  llm_processed: 'LLM-Analyse',
  user_modified: 'Benutzer geändert',
  read: 'Gelesen',
  archived: 'Archiviert'
}

const formatEventType = (type: string): string => {
  return eventTypeLabels[type] || type
}

const formatTimestamp = (ts: string): string => {
  return new Date(ts).toLocaleString('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  })
}

const formatEventData = (data: Record<string, unknown> | null): string => {
  if (!data) return ''
  const parts: string[] = []
  if (data.priority) parts.push(`Priorität: ${data.priority}`)
  if (data.assigned_aks && Array.isArray(data.assigned_aks) && data.assigned_aks.length > 0) {
    parts.push(`AKs: ${data.assigned_aks.join(', ')}`)
  }
  if (data.relevance_score !== undefined) {
    parts.push(`Relevanz: ${Math.round((data.relevance_score as number) * 100)}%`)
  }
  return parts.join(' | ')
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
          class="p-1.5 rounded text-gray-500 hover:text-purple-600 hover:bg-purple-100 transition-colors"
          title="Verlauf anzeigen"
          :disabled="historyLoading"
          @click="loadHistory"
        >
          <ClockIcon class="h-5 w-5" :class="{ 'animate-spin': historyLoading }" />
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

    <!-- History Popup -->
    <div
      v-if="showHistory"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50 p-4"
      @click.self="showHistory = false"
    >
      <div class="w-full max-w-lg max-h-[80vh] rounded-lg bg-white shadow-xl overflow-hidden flex flex-col">
        <div class="flex items-center justify-between border-b border-gray-200 p-4">
          <h3 class="font-medium text-gray-900">
            <ClockIcon class="inline h-5 w-5 mr-2" />
            Verlauf
          </h3>
          <button
            type="button"
            class="text-gray-400 hover:text-gray-600"
            @click="showHistory = false"
          >
            <XMarkIcon class="h-5 w-5" />
          </button>
        </div>
        <div class="flex-1 overflow-y-auto p-4">
          <div v-if="historyEvents.length === 0" class="text-sm text-gray-500 text-center py-4">
            Keine Ereignisse vorhanden
          </div>
          <div v-else class="space-y-3">
            <div
              v-for="event in historyEvents"
              :key="event.id"
              class="border-l-2 border-gray-200 pl-3 py-1"
            >
              <div class="flex items-center gap-2">
                <span class="text-sm font-medium text-gray-900">
                  {{ formatEventType(event.event_type) }}
                </span>
                <span class="text-xs text-gray-500">
                  {{ formatTimestamp(event.timestamp) }}
                </span>
              </div>
              <div v-if="formatEventData(event.data)" class="text-xs text-gray-600 mt-0.5">
                {{ formatEventData(event.data) }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

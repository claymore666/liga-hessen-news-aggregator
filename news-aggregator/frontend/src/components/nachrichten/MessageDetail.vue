<script setup lang="ts">
import { ref } from 'vue'
import type { Item } from '@/types'
import SourceIcon from '@/components/SourceIcon.vue'
import { ArrowTopRightOnSquareIcon, ChevronRightIcon } from '@heroicons/vue/24/outline'
import { format } from 'date-fns'
import { de } from 'date-fns/locale'

defineProps<{
  item: Item
}>()

const summaryOpen = ref(true)
const analysisOpen = ref(true)
const contentOpen = ref(false)

const formatDate = (date: string | null) => {
  if (!date) return 'Unbekannt'
  return format(new Date(date), "d. MMMM yyyy 'um' HH:mm 'Uhr'", { locale: de })
}
</script>

<template>
  <div class="space-y-4 overflow-y-auto flex-1 pr-1">
    <!-- Header -->
    <div>
      <h2 class="text-lg font-bold text-gray-900 leading-tight">
        {{ item.title }}
      </h2>
      <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-gray-600">
        <span class="flex items-center gap-1.5">
          <SourceIcon v-if="item.channel" :connector-type="item.channel.connector_type" size="sm" />
          {{ item.source?.name ?? 'Unbekannt' }}
        </span>
        <span>&middot;</span>
        <span>{{ formatDate(item.published_at) }}</span>
        <span v-if="item.author">&middot;</span>
        <span v-if="item.author">{{ item.author }}</span>
      </div>
      <div class="mt-2">
        <a
          :href="item.url"
          target="_blank"
          rel="noopener noreferrer"
          class="inline-flex items-center gap-1 text-sm text-blue-600 hover:text-blue-800 hover:underline"
        >
          <ArrowTopRightOnSquareIcon class="h-4 w-4" />
          Artikel öffnen
        </a>
      </div>
    </div>

    <!-- Relevance Score -->
    <div class="rounded-lg bg-gray-50 px-3 py-2 text-sm">
      <span class="font-medium text-gray-700">Relevanz-Score:</span>
      <span class="ml-1 text-gray-600">{{ item.priority_score }}/100</span>
    </div>

    <!-- Summary -->
    <details v-if="item.summary" class="group" :open="summaryOpen" @toggle="summaryOpen = ($event.target as HTMLDetailsElement).open">
      <summary class="flex items-center gap-2 cursor-pointer text-sm font-semibold text-gray-800 hover:text-gray-600 list-none">
        <ChevronRightIcon class="h-4 w-4 transition-transform group-open:rotate-90" />
        Zusammenfassung
      </summary>
      <p class="mt-2 text-sm text-gray-700 pl-6">
        {{ item.summary }}
      </p>
    </details>

    <!-- Analysis -->
    <details v-if="item.detailed_analysis" class="group" :open="analysisOpen" @toggle="analysisOpen = ($event.target as HTMLDetailsElement).open">
      <summary class="flex items-center gap-2 cursor-pointer text-sm font-semibold text-gray-800 hover:text-gray-600 list-none">
        <ChevronRightIcon class="h-4 w-4 transition-transform group-open:rotate-90" />
        Analyse
      </summary>
      <div class="mt-2 text-sm text-gray-700 pl-6 whitespace-pre-wrap">
        {{ item.detailed_analysis }}
      </div>
    </details>

    <!-- Tags -->
    <div v-if="item.tags?.length" class="pl-6">
      <div class="flex flex-wrap gap-1.5">
        <span
          v-for="tag in item.tags"
          :key="tag"
          class="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-700"
        >
          {{ tag }}
        </span>
      </div>
    </div>

    <!-- Original Content -->
    <details class="group" :open="contentOpen" @toggle="contentOpen = ($event.target as HTMLDetailsElement).open">
      <summary class="flex items-center gap-2 cursor-pointer text-sm font-semibold text-gray-800 hover:text-gray-600 list-none">
        <ChevronRightIcon class="h-4 w-4 transition-transform group-open:rotate-90" />
        Originalinhalt
      </summary>
      <div class="mt-2 text-sm text-gray-600 pl-6 whitespace-pre-wrap max-h-96 overflow-y-auto">
        {{ item.content }}
      </div>
    </details>
  </div>
</template>

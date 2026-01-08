<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useItemsStore } from '@/stores'
import {
  ArrowLeftIcon,
  ArrowTopRightOnSquareIcon,
  CheckIcon,
  ArchiveBoxIcon,
  EnvelopeIcon
} from '@heroicons/vue/24/outline'
import PriorityBadge from '@/components/PriorityBadge.vue'
import SourceIcon from '@/components/SourceIcon.vue'
import { format } from 'date-fns'
import { de } from 'date-fns/locale'

const route = useRoute()
const router = useRouter()
const itemsStore = useItemsStore()

const itemId = ref(parseInt(route.params.id as string))

const formatDate = (date: string | null) => {
  if (!date) return 'Unbekannt'
  return format(new Date(date), "d. MMMM yyyy 'um' HH:mm 'Uhr'", { locale: de })
}

const goBack = () => {
  router.back()
}

const toggleRead = async () => {
  if (itemsStore.currentItem?.is_read) {
    await itemsStore.markAsUnread(itemId.value)
  } else {
    await itemsStore.markAsRead(itemId.value)
  }
}

const archiveItem = async () => {
  await itemsStore.archiveItem(itemId.value)
  router.push('/items')
}

onMounted(async () => {
  await itemsStore.fetchItem(itemId.value)
  if (itemsStore.currentItem && !itemsStore.currentItem.is_read) {
    await itemsStore.markAsRead(itemId.value)
  }
})
</script>

<template>
  <div class="space-y-4">
    <button
      type="button"
      class="flex items-center text-sm text-gray-500 hover:text-gray-700"
      @click="goBack"
    >
      <ArrowLeftIcon class="mr-2 h-4 w-4" />
      Zurück zur Liste
    </button>

    <div v-if="itemsStore.loading" class="card py-12 text-center">
      <p class="text-gray-500">Lade Nachricht...</p>
    </div>

    <template v-else-if="itemsStore.currentItem">
      <div class="card">
        <div class="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div class="flex items-center gap-3">
              <PriorityBadge :priority="itemsStore.currentItem.priority" />
              <span
                v-if="itemsStore.currentItem.assigned_ak"
                class="badge bg-purple-100 text-purple-800"
              >
                {{ itemsStore.currentItem.assigned_ak }}
              </span>
            </div>
            <h1 class="mt-3 text-2xl font-bold text-gray-900">
              {{ itemsStore.currentItem.title }}
            </h1>
            <p class="mt-2 text-sm text-gray-500">
              <span v-if="itemsStore.currentItem.author">
                {{ itemsStore.currentItem.author }} &middot;
              </span>
              {{ formatDate(itemsStore.currentItem.published_at) }}
            </p>
          </div>

          <div class="flex gap-2">
            <button
              type="button"
              class="btn btn-secondary"
              @click="toggleRead"
            >
              <CheckIcon class="mr-2 h-4 w-4" />
              {{ itemsStore.currentItem.is_read ? 'Als ungelesen' : 'Als gelesen' }}
            </button>
            <button
              type="button"
              class="btn btn-secondary"
              @click="archiveItem"
            >
              <ArchiveBoxIcon class="mr-2 h-4 w-4" />
              Archivieren
            </button>
          </div>
        </div>

        <!-- Source Info -->
        <div class="mt-4 rounded-lg bg-gray-50 p-3">
          <p class="flex items-center gap-2 text-sm">
            <span class="font-medium text-gray-700">Quelle:</span>
            <SourceIcon v-if="itemsStore.currentItem.channel" :connector-type="itemsStore.currentItem.channel.connector_type" size="sm" />
            <span class="text-gray-600">
              {{ itemsStore.currentItem.source?.name ?? 'Unbekannt' }}
            </span>
          </p>
          <p class="mt-1 text-sm">
            <span class="font-medium text-gray-700">Relevanz-Score:</span>
            <span class="text-gray-600">
              {{ itemsStore.currentItem.priority_score }}/100
            </span>
          </p>
        </div>

        <!-- Summary (short, 2-3 sentences) -->
        <div v-if="itemsStore.currentItem.summary" class="mt-6">
          <h2 class="text-lg font-medium text-gray-900">Zusammenfassung</h2>
          <p class="mt-2 text-gray-700">
            {{ itemsStore.currentItem.summary }}
          </p>
        </div>

        <!-- Detailed Analysis (5-8 sentences) -->
        <div v-if="itemsStore.currentItem.detailed_analysis" class="mt-6">
          <h2 class="text-lg font-medium text-gray-900">Analyse</h2>
          <div class="prose prose-sm mt-2 max-w-none text-gray-700">
            {{ itemsStore.currentItem.detailed_analysis }}
          </div>
        </div>

        <!-- Original Content (collapsible) -->
        <div class="mt-6">
          <details class="group">
            <summary class="text-lg font-medium text-gray-900 cursor-pointer hover:text-gray-700 list-none flex items-center gap-2">
              <svg class="w-4 h-4 transition-transform group-open:rotate-90" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
              </svg>
              Originalinhalt
            </summary>
            <div class="prose prose-sm mt-2 max-w-none text-gray-600">
              {{ itemsStore.currentItem.content }}
            </div>
          </details>
        </div>

        <!-- Tags -->
        <div v-if="itemsStore.currentItem.tags?.length" class="mt-6">
          <h2 class="text-lg font-medium text-gray-900">Tags</h2>
          <div class="mt-2 flex flex-wrap gap-2">
            <span
              v-for="tag in itemsStore.currentItem.tags"
              :key="tag"
              class="badge bg-gray-100 text-gray-700"
            >
              {{ tag }}
            </span>
          </div>
        </div>

        <!-- Actions -->
        <div class="mt-8 flex flex-wrap gap-3 border-t border-gray-200 pt-6">
          <a
            :href="itemsStore.currentItem.url"
            target="_blank"
            rel="noopener noreferrer"
            class="btn btn-primary"
          >
            <ArrowTopRightOnSquareIcon class="mr-2 h-4 w-4" />
            Artikel öffnen
          </a>
          <button type="button" class="btn btn-secondary">
            <EnvelopeIcon class="mr-2 h-4 w-4" />
            Per E-Mail teilen
          </button>
        </div>
      </div>
    </template>

    <div v-else class="card py-12 text-center">
      <p class="text-gray-500">Nachricht nicht gefunden</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useSourcesStore } from '@/stores'
import {
  PlusIcon,
  ArrowPathIcon,
  PencilIcon,
  TrashIcon,
  CheckCircleIcon,
  XCircleIcon,
  ExclamationCircleIcon
} from '@heroicons/vue/24/outline'
import { formatDistanceToNow } from 'date-fns'
import { de } from 'date-fns/locale'
import SourceFormModal from '@/components/SourceFormModal.vue'
import type { Source } from '@/types'

const sourcesStore = useSourcesStore()
const showCreateModal = ref(false)
const editingSource = ref<Source | null>(null)

const formatTime = (date: string | null) => {
  if (!date) return 'Nie'
  return formatDistanceToNow(new Date(date), { addSuffix: true, locale: de })
}

const connectorLabels: Record<string, string> = {
  rss: 'RSS/Atom',
  html: 'HTML Scraper',
  bluesky: 'Bluesky',
  twitter: 'Twitter/X',
  pdf: 'PDF',
  mastodon: 'Mastodon'
}

const deleteSource = async (source: Source) => {
  if (confirm(`Quelle "${source.name}" wirklich löschen?`)) {
    await sourcesStore.deleteSource(source.id)
  }
}

const onSourceSaved = () => {
  showCreateModal.value = false
  editingSource.value = null
}

onMounted(() => {
  sourcesStore.fetchSources()
})
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-bold text-gray-900">Quellen</h1>
        <p class="text-sm text-gray-500">
          {{ sourcesStore.sources.length }} Quellen konfiguriert
        </p>
      </div>
      <div class="flex gap-2">
        <button
          type="button"
          class="btn btn-secondary"
          :disabled="sourcesStore.fetching !== null"
          @click="sourcesStore.triggerFetchAll()"
        >
          <ArrowPathIcon
            class="mr-2 h-4 w-4"
            :class="{ 'animate-spin': sourcesStore.fetching !== null }"
          />
          Alle abrufen
        </button>
        <button
          type="button"
          class="btn btn-primary"
          @click="showCreateModal = true"
        >
          <PlusIcon class="mr-2 h-4 w-4" />
          Neue Quelle
        </button>
      </div>
    </div>

    <div v-if="sourcesStore.loading" class="card py-12 text-center">
      <ArrowPathIcon class="mx-auto h-8 w-8 animate-spin text-gray-400" />
    </div>

    <div v-else-if="sourcesStore.sources.length === 0" class="card py-12 text-center">
      <p class="text-gray-500">Keine Quellen konfiguriert</p>
      <button
        type="button"
        class="btn btn-primary mt-4"
        @click="showCreateModal = true"
      >
        Erste Quelle hinzufügen
      </button>
    </div>

    <div v-else class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      <div v-for="source in sourcesStore.sources" :key="source.id" class="card">
        <div class="flex items-start justify-between">
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2">
              <component
                :is="source.enabled ? CheckCircleIcon : XCircleIcon"
                class="h-5 w-5 flex-shrink-0"
                :class="source.enabled ? 'text-green-500' : 'text-gray-400'"
              />
              <h3 class="truncate text-lg font-medium text-gray-900">
                {{ source.name }}
              </h3>
            </div>
            <p class="mt-1 truncate text-sm text-gray-500">
              {{ source.url }}
            </p>
          </div>
        </div>

        <div class="mt-4 flex flex-wrap gap-2">
          <span class="badge bg-liga-100 text-liga-700">
            {{ connectorLabels[source.connector_type] || source.connector_type }}
          </span>
          <span class="badge bg-gray-100 text-gray-700">
            Alle {{ source.fetch_interval }} Min.
          </span>
        </div>

        <div class="mt-4 space-y-1 text-sm text-gray-500">
          <p>
            Letzter Abruf: {{ formatTime(source.last_fetched_at) }}
          </p>
          <p v-if="source.error_count > 0" class="flex items-center gap-1 text-red-600">
            <ExclamationCircleIcon class="h-4 w-4" />
            {{ source.error_count }} Fehler
          </p>
        </div>

        <div class="mt-4 flex gap-2 border-t border-gray-200 pt-4">
          <button
            type="button"
            class="btn btn-secondary flex-1 text-sm"
            :disabled="sourcesStore.fetching === source.id"
            @click="sourcesStore.triggerFetch(source.id)"
          >
            <ArrowPathIcon
              class="mr-1 h-4 w-4"
              :class="{ 'animate-spin': sourcesStore.fetching === source.id }"
            />
            Abrufen
          </button>
          <button
            type="button"
            class="btn btn-secondary text-sm"
            @click="editingSource = source"
          >
            <PencilIcon class="h-4 w-4" />
          </button>
          <button
            type="button"
            class="btn btn-danger text-sm"
            @click="deleteSource(source)"
          >
            <TrashIcon class="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>

    <!-- Create/Edit Modal -->
    <SourceFormModal
      v-if="showCreateModal || editingSource"
      :source="editingSource"
      @close="showCreateModal = false; editingSource = null"
      @saved="onSourceSaved"
    />
  </div>
</template>

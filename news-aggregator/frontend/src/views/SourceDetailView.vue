<script setup lang="ts">
import { onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSourcesStore } from '@/stores'
import { ArrowLeftIcon } from '@heroicons/vue/24/outline'
import SourceIcon from '@/components/SourceIcon.vue'

const route = useRoute()
const router = useRouter()
const sourcesStore = useSourcesStore()

onMounted(() => {
  sourcesStore.fetchSource(parseInt(route.params.id as string))
})
</script>

<template>
  <div class="space-y-4">
    <button
      type="button"
      class="flex items-center text-sm text-gray-500 hover:text-gray-700"
      @click="router.back()"
    >
      <ArrowLeftIcon class="mr-2 h-4 w-4" />
      Zurück zu Quellen
    </button>

    <div v-if="sourcesStore.currentSource" class="card">
      <h1 class="text-2xl font-bold text-gray-900">
        {{ sourcesStore.currentSource.name }}
      </h1>
      <p v-if="sourcesStore.currentSource.description" class="mt-2 text-gray-600">
        {{ sourcesStore.currentSource.description }}
      </p>

      <!-- Channels -->
      <div v-if="sourcesStore.currentSource.channels?.length" class="mt-4">
        <h2 class="text-lg font-semibold text-gray-800 mb-2">Kanäle</h2>
        <ul class="space-y-2">
          <li
            v-for="channel in sourcesStore.currentSource.channels"
            :key="channel.id"
            class="flex items-center gap-3 rounded-lg bg-gray-50 p-3"
          >
            <SourceIcon :connector-type="channel.connector_type" size="sm" />
            <div class="flex-1 min-w-0">
              <p class="font-medium text-gray-900 truncate">
                {{ channel.name || channel.connector_type }}
              </p>
              <p class="text-sm text-gray-500 truncate">
                {{ channel.source_identifier || '-' }}
              </p>
            </div>
            <span
              :class="channel.enabled ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'"
              class="rounded-full px-2 py-0.5 text-xs font-medium"
            >
              {{ channel.enabled ? 'Aktiv' : 'Inaktiv' }}
            </span>
          </li>
        </ul>
      </div>

      <p v-else class="mt-4 text-gray-500">
        Keine Kanäle konfiguriert
      </p>
    </div>
  </div>
</template>

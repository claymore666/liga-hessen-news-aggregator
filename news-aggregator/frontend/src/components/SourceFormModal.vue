<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useSourcesStore } from '@/stores'
import { connectorsApi } from '@/api'
import { XMarkIcon } from '@heroicons/vue/24/outline'
import type { Source, ConnectorType, ConnectorInfo } from '@/types'

const props = defineProps<{
  source?: Source | null
}>()

const emit = defineEmits<{
  close: []
  saved: []
}>()

const sourcesStore = useSourcesStore()

const connectors = ref<ConnectorInfo[]>([])
const loading = ref(false)
const validating = ref(false)
const validationResult = ref<{ valid: boolean; message: string } | null>(null)

const form = ref({
  name: props.source?.name ?? '',
  url: (props.source?.config?.url || props.source?.config?.feed_url || props.source?.config?.handle || '') as string,
  connector_type: (props.source?.connector_type ?? 'rss') as ConnectorType,
  connector_config: props.source?.config ?? {},
  enabled: props.source?.enabled ?? true,
  fetch_interval: props.source?.fetch_interval_minutes ?? 60
})

const isEditing = computed(() => !!props.source)

const currentConnector = computed(() =>
  connectors.value.find((c) => c.type === form.value.connector_type)
)

const validateConfig = async () => {
  validating.value = true
  validationResult.value = null
  try {
    const response = await connectorsApi.validate(
      form.value.connector_type,
      { url: form.value.url, ...form.value.connector_config }
    )
    validationResult.value = response.data
  } catch {
    validationResult.value = { valid: false, message: 'Validierung fehlgeschlagen' }
  } finally {
    validating.value = false
  }
}

const save = async () => {
  loading.value = true
  try {
    if (isEditing.value && props.source) {
      await sourcesStore.updateSource(props.source.id, form.value)
    } else {
      await sourcesStore.createSource(form.value)
    }
    emit('saved')
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  const response = await connectorsApi.list()
  connectors.value = response.data
})
</script>

<template>
  <div class="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50 p-4">
    <div class="w-full max-w-lg rounded-lg bg-white shadow-xl">
      <div class="flex items-center justify-between border-b border-gray-200 p-4">
        <h2 class="text-lg font-medium text-gray-900">
          {{ isEditing ? 'Quelle bearbeiten' : 'Neue Quelle' }}
        </h2>
        <button
          type="button"
          class="text-gray-400 hover:text-gray-600"
          @click="emit('close')"
        >
          <XMarkIcon class="h-5 w-5" />
        </button>
      </div>

      <form class="p-4" @submit.prevent="save">
        <div class="space-y-4">
          <div>
            <label class="block text-sm font-medium text-gray-700">Name</label>
            <input
              v-model="form.name"
              type="text"
              class="input mt-1"
              required
              placeholder="z.B. Hessenschau"
            />
          </div>

          <div>
            <label class="block text-sm font-medium text-gray-700">Connector-Typ</label>
            <select v-model="form.connector_type" class="input mt-1">
              <option v-for="c in connectors" :key="c.type" :value="c.type">
                {{ c.name }}
              </option>
            </select>
            <p v-if="currentConnector" class="mt-1 text-xs text-gray-500">
              {{ currentConnector.description }}
            </p>
          </div>

          <div>
            <label class="block text-sm font-medium text-gray-700">URL</label>
            <input
              v-model="form.url"
              type="url"
              class="input mt-1"
              required
              placeholder="https://example.com/feed.xml"
            />
          </div>

          <div>
            <label class="block text-sm font-medium text-gray-700">
              Abrufintervall (Minuten)
            </label>
            <input
              v-model.number="form.fetch_interval"
              type="number"
              min="5"
              max="1440"
              class="input mt-1"
            />
          </div>

          <div class="flex items-center gap-2">
            <input
              v-model="form.enabled"
              type="checkbox"
              class="rounded border-gray-300"
            />
            <label class="text-sm text-gray-700">Quelle aktivieren</label>
          </div>

          <!-- Validation -->
          <div class="border-t border-gray-200 pt-4">
            <button
              type="button"
              class="btn btn-secondary w-full"
              :disabled="validating || !form.url"
              @click="validateConfig"
            >
              {{ validating ? 'Prüfe...' : 'Verbindung testen' }}
            </button>
            <div
              v-if="validationResult"
              class="mt-2 rounded p-2 text-sm"
              :class="validationResult.valid ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'"
            >
              {{ validationResult.message }}
            </div>
          </div>
        </div>

        <div class="mt-6 flex justify-end gap-3">
          <button
            type="button"
            class="btn btn-secondary"
            @click="emit('close')"
          >
            Abbrechen
          </button>
          <button
            type="submit"
            class="btn btn-primary"
            :disabled="loading"
          >
            {{ loading ? 'Speichere...' : (isEditing ? 'Speichern' : 'Erstellen') }}
          </button>
        </div>
      </form>
    </div>
  </div>
</template>

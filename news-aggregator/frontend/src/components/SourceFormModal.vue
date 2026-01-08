<script setup lang="ts">
import { ref, computed } from 'vue'
import { useSourcesStore } from '@/stores'
import { XMarkIcon } from '@heroicons/vue/24/outline'
import type { Source } from '@/types'

const props = defineProps<{
  source?: Source | null
}>()

const emit = defineEmits<{
  close: []
  saved: []
}>()

const sourcesStore = useSourcesStore()
const loading = ref(false)

const form = ref({
  name: props.source?.name ?? '',
  description: props.source?.description ?? '',
  is_stakeholder: props.source?.is_stakeholder ?? false,
  enabled: props.source?.enabled ?? true
})

const isEditing = computed(() => !!props.source)

const save = async () => {
  loading.value = true
  try {
    if (isEditing.value && props.source) {
      await sourcesStore.updateSource(props.source.id, {
        name: form.value.name,
        description: form.value.description || null,
        is_stakeholder: form.value.is_stakeholder,
        enabled: form.value.enabled
      })
    } else {
      await sourcesStore.createSource({
        name: form.value.name,
        description: form.value.description || null,
        is_stakeholder: form.value.is_stakeholder,
        enabled: form.value.enabled,
        channels: []
      })
    }
    emit('saved')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50 p-4">
    <div class="w-full max-w-lg rounded-lg bg-white shadow-xl">
      <div class="flex items-center justify-between border-b border-gray-200 p-4">
        <h2 class="text-lg font-medium text-gray-900">
          {{ isEditing ? 'Organisation bearbeiten' : 'Neue Organisation' }}
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
            <label class="block text-sm font-medium text-gray-700">Name *</label>
            <input
              v-model="form.name"
              type="text"
              class="input mt-1"
              required
              placeholder="z.B. BMAS (Arbeitsministerium)"
            />
            <p class="mt-1 text-xs text-gray-500">
              Name der Organisation (z.B. Ministerium, Verband, Medium)
            </p>
          </div>

          <div>
            <label class="block text-sm font-medium text-gray-700">Beschreibung</label>
            <textarea
              v-model="form.description"
              class="input mt-1"
              rows="2"
              placeholder="Optionale Beschreibung der Organisation..."
            />
          </div>

          <div class="flex items-center gap-2">
            <input
              id="is_stakeholder"
              v-model="form.is_stakeholder"
              type="checkbox"
              class="rounded border-gray-300"
            />
            <label for="is_stakeholder" class="text-sm text-gray-700">
              Stakeholder
            </label>
            <span class="text-xs text-gray-500">
              (wichtige Organisation für Liga)
            </span>
          </div>

          <div class="flex items-center gap-2">
            <input
              id="enabled"
              v-model="form.enabled"
              type="checkbox"
              class="rounded border-gray-300"
            />
            <label for="enabled" class="text-sm text-gray-700">
              Organisation aktivieren
            </label>
            <span class="text-xs text-gray-500">
              (Master-Schalter für alle Kanäle)
            </span>
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
            :disabled="loading || !form.name.trim()"
          >
            {{ loading ? 'Speichere...' : isEditing ? 'Speichern' : 'Erstellen' }}
          </button>
        </div>
      </form>

      <div v-if="!isEditing" class="border-t border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-600">
        Nach dem Erstellen können Sie Kanäle (RSS, X.com, etc.) hinzufügen.
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { adminApi, type SystemStatsResponse } from '@/api'
import { ExclamationTriangleIcon, XMarkIcon } from '@heroicons/vue/24/outline'

const systemStats = ref<SystemStatsResponse | null>(null)
const dismissed = ref<Set<string>>(new Set())
const loading = ref(false)
let pollInterval: number | null = null

const alerts = computed(() => {
  if (!systemStats.value) return []

  const result: Array<{ id: string; type: 'error' | 'warning'; title: string; message: string }> = []

  // Check LLM worker
  const llm = systemStats.value.llm_worker
  if (llm.stopped_due_to_errors && !dismissed.value.has('llm_error')) {
    result.push({
      id: 'llm_error',
      type: 'error',
      title: 'LLM Worker gestoppt',
      message: 'Der LLM Worker wurde nach wiederholten Fehlern gestoppt. Bitte Logs prüfen und manuell neu starten.'
    })
  } else if (!llm.running && !llm.stopped_due_to_errors && !dismissed.value.has('llm_stopped')) {
    result.push({
      id: 'llm_stopped',
      type: 'warning',
      title: 'LLM Worker inaktiv',
      message: 'Der LLM Worker ist nicht gestartet. Neue Artikel werden nicht analysiert.'
    })
  }

  // Check Classifier worker
  const clf = systemStats.value.classifier_worker
  if (clf.stopped_due_to_errors && !dismissed.value.has('classifier_error')) {
    result.push({
      id: 'classifier_error',
      type: 'error',
      title: 'Classifier Worker gestoppt',
      message: 'Der Classifier Worker wurde nach wiederholten Fehlern gestoppt. Bitte Logs prüfen und manuell neu starten.'
    })
  } else if (!clf.running && !clf.stopped_due_to_errors && !dismissed.value.has('classifier_stopped')) {
    result.push({
      id: 'classifier_stopped',
      type: 'warning',
      title: 'Classifier Worker inaktiv',
      message: 'Der Classifier Worker ist nicht gestartet. Neue Artikel werden nicht klassifiziert.'
    })
  }

  // Check Scheduler
  const scheduler = systemStats.value.scheduler
  if (!scheduler.running && !dismissed.value.has('scheduler_stopped')) {
    result.push({
      id: 'scheduler_stopped',
      type: 'warning',
      title: 'Scheduler inaktiv',
      message: 'Der Scheduler ist nicht gestartet. Automatische Abfragen sind deaktiviert.'
    })
  }

  return result
})

const hasAlerts = computed(() => alerts.value.length > 0)

async function fetchStatus() {
  if (loading.value) return
  loading.value = true
  try {
    const response = await adminApi.getStats()
    systemStats.value = response.data
  } catch (e) {
    console.error('Failed to fetch system stats:', e)
  } finally {
    loading.value = false
  }
}

function dismiss(id: string) {
  dismissed.value.add(id)
}

onMounted(() => {
  fetchStatus()
  // Poll every 30 seconds
  pollInterval = window.setInterval(fetchStatus, 30000)
})

onUnmounted(() => {
  if (pollInterval) {
    clearInterval(pollInterval)
  }
})
</script>

<template>
  <div v-if="hasAlerts" class="space-y-2 mb-4">
    <div
      v-for="alert in alerts"
      :key="alert.id"
      class="rounded-lg p-3 flex items-start gap-3"
      :class="{
        'bg-red-100 border border-red-300': alert.type === 'error',
        'bg-yellow-100 border border-yellow-300': alert.type === 'warning'
      }"
    >
      <ExclamationTriangleIcon
        class="h-5 w-5 flex-shrink-0 mt-0.5"
        :class="{
          'text-red-600': alert.type === 'error',
          'text-yellow-600': alert.type === 'warning'
        }"
      />
      <div class="flex-1 min-w-0">
        <p
          class="text-sm font-medium"
          :class="{
            'text-red-800': alert.type === 'error',
            'text-yellow-800': alert.type === 'warning'
          }"
        >
          {{ alert.title }}
        </p>
        <p
          class="text-sm mt-0.5"
          :class="{
            'text-red-700': alert.type === 'error',
            'text-yellow-700': alert.type === 'warning'
          }"
        >
          {{ alert.message }}
        </p>
      </div>
      <button
        type="button"
        class="flex-shrink-0 p-1 rounded hover:bg-white/50 transition-colors"
        :class="{
          'text-red-600': alert.type === 'error',
          'text-yellow-600': alert.type === 'warning'
        }"
        title="Ausblenden"
        @click="dismiss(alert.id)"
      >
        <XMarkIcon class="h-4 w-4" />
      </button>
    </div>
  </div>
</template>

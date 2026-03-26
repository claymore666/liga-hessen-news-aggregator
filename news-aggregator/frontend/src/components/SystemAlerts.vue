<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { adminApi, type SystemStatsResponse } from '@/api'
import { ExclamationTriangleIcon, XMarkIcon, ArrowPathIcon } from '@heroicons/vue/24/outline'

const systemStats = ref<SystemStatsResponse | null>(null)
const dismissed = ref<Set<string>>(new Set())
const loading = ref(false)
const resuming = ref<Set<string>>(new Set())
let pollInterval: number | null = null

interface Alert {
  id: string
  type: 'error' | 'warning'
  title: string
  message: string
  resumeAction?: string  // worker name for resume button
}

const alerts = computed(() => {
  if (!systemStats.value) return []

  const result: Alert[] = []

  // Check LLM worker
  const llm = systemStats.value.llm_worker
  if (llm.stopped_due_to_errors && !dismissed.value.has('llm_errors')) {
    result.push({
      id: 'llm_errors',
      type: 'error',
      title: 'LLM Worker fehlerhaft',
      message: 'Zu viele Fehler. Verarbeitung pausiert, Wiederholung alle 5 Min.',
      resumeAction: 'llm-worker',
    })
  } else if (!llm.running && !dismissed.value.has('llm_stopped')) {
    result.push({
      id: 'llm_stopped',
      type: 'warning',
      title: 'LLM Worker inaktiv',
      message: 'Der LLM Worker ist nicht gestartet. Neue Artikel werden nicht analysiert.'
    })
  }

  // Check Classifier worker
  // Note: service_available=false (gpu1 offline) is normal — no alert needed
  const clf = systemStats.value.classifier_worker
  if (clf.stopped_due_to_errors && !dismissed.value.has('classifier_errors')) {
    result.push({
      id: 'classifier_errors',
      type: 'error',
      title: 'Classifier Worker fehlerhaft',
      message: 'Zu viele Fehler. Klassifizierung pausiert, Wiederholung alle 5 Min.',
      resumeAction: 'classifier-worker',
    })
  } else if (!clf.running && clf.service_available !== false && !dismissed.value.has('classifier_stopped')) {
    result.push({
      id: 'classifier_stopped',
      type: 'warning',
      title: 'Classifier Worker inaktiv',
      message: 'Der Classifier Worker ist nicht gestartet. Neue Artikel werden nicht klassifiziert.'
    })
  }

  // Check Dedup worker
  const dedup = systemStats.value.dedup_worker
  if (dedup.stopped_due_to_errors && !dismissed.value.has('dedup_errors')) {
    result.push({
      id: 'dedup_errors',
      type: 'error',
      title: 'Dedup Worker fehlerhaft',
      message: 'Zu viele Fehler. Dublettenprüfung pausiert, Wiederholung alle 5 Min.',
      resumeAction: 'dedup-worker',
    })
  } else if (!dedup.running && !dismissed.value.has('dedup_stopped')) {
    result.push({
      id: 'dedup_stopped',
      type: 'warning',
      title: 'Dedup Worker inaktiv',
      message: 'Der Dedup Worker ist nicht gestartet. Neue Artikel werden nicht auf Dubletten geprüft.'
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
    // Auto-clear dismissed error alerts when error state resolves
    for (const key of ['llm_errors', 'classifier_errors', 'dedup_errors']) {
      if (dismissed.value.has(key)) {
        const worker = key.replace('_errors', '_worker') as keyof Pick<SystemStatsResponse, 'llm_worker' | 'classifier_worker' | 'dedup_worker'>
        if (systemStats.value[worker] && !systemStats.value[worker].stopped_due_to_errors) {
          dismissed.value.delete(key)
        }
      }
    }
  } catch (e) {
    console.error('Failed to fetch system stats:', e)
  } finally {
    loading.value = false
  }
}

const resumeActions: Record<string, () => Promise<unknown>> = {
  'llm-worker': () => adminApi.resumeLlmWorker(),
  'classifier-worker': () => adminApi.resumeClassifierWorker(),
  'dedup-worker': () => adminApi.resumeDedupWorker(),
}

async function resumeWorker(workerName: string, alertId: string) {
  if (resuming.value.has(workerName)) return
  resuming.value.add(workerName)
  try {
    const action = resumeActions[workerName]
    if (action) await action()
    dismissed.value.add(alertId)
    setTimeout(fetchStatus, 2000)
  } catch (e) {
    console.error(`Failed to resume ${workerName}:`, e)
  } finally {
    resuming.value.delete(workerName)
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
        v-if="alert.resumeAction"
        type="button"
        class="flex-shrink-0 px-2.5 py-1 rounded text-xs font-medium bg-white border border-red-300 text-red-700 hover:bg-red-50 transition-colors disabled:opacity-50"
        :disabled="resuming.has(alert.resumeAction)"
        title="Worker fortsetzen"
        @click="resumeWorker(alert.resumeAction!, alert.id)"
      >
        <ArrowPathIcon
          class="h-3.5 w-3.5 inline-block mr-1"
          :class="{ 'animate-spin': resuming.has(alert.resumeAction) }"
        />
        Fortsetzen
      </button>
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

<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'
import {
  ArrowPathIcon,
  PlayIcon,
  StopIcon,
  PauseIcon,
  CheckCircleIcon,
  XCircleIcon,
  ClockIcon,
  ExclamationTriangleIcon
} from '@heroicons/vue/24/outline'
import { adminApi, type SystemStatsResponse } from '@/api'

const stats = ref<SystemStatsResponse | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const actionLoading = ref<string | null>(null)
const autoRefresh = ref(true)
const refreshInterval = ref(30)
let refreshTimer: number | null = null

const fetchStats = async () => {
  try {
    error.value = null
    const response = await adminApi.getStats()
    stats.value = response.data
  } catch (e) {
    error.value = e instanceof Error ? e.message : 'Fehler beim Laden der Statistiken'
  } finally {
    loading.value = false
  }
}

const startAutoRefresh = () => {
  if (refreshTimer) clearInterval(refreshTimer)
  if (autoRefresh.value) {
    refreshTimer = window.setInterval(fetchStats, refreshInterval.value * 1000)
  }
}

const toggleAutoRefresh = () => {
  autoRefresh.value = !autoRefresh.value
  startAutoRefresh()
}

// Worker control actions
const controlAction = async (action: () => Promise<unknown>, name: string) => {
  actionLoading.value = name
  try {
    await action()
    await fetchStats()
  } catch (e) {
    error.value = e instanceof Error ? e.message : `Fehler bei ${name}`
  } finally {
    actionLoading.value = null
  }
}

// Computed values for display
const nextSchedulerRun = computed(() => {
  if (!stats.value?.scheduler.jobs.length) return null
  const fetchJob = stats.value.scheduler.jobs.find(j => j.id === 'fetch_due_channels')
  if (!fetchJob?.next_run) return null
  const nextRun = new Date(fetchJob.next_run)
  const now = new Date()
  const diffMs = nextRun.getTime() - now.getTime()
  const diffMins = Math.round(diffMs / 60000)
  if (diffMins <= 0) return 'Jetzt'
  if (diffMins === 1) return '1 Minute'
  return `${diffMins} Minuten`
})

const queuePriorities = computed(() => {
  if (!stats.value) return []
  const priorities = stats.value.processing_queue.by_retry_priority
  return [
    { key: 'high', label: 'Hoch', count: priorities['high'] || 0, color: 'bg-red-500' },
    { key: 'edge_case', label: 'Edge', count: priorities['edge_case'] || 0, color: 'bg-yellow-500' },
    { key: 'unknown', label: 'Unbekannt', count: priorities['unknown'] || 0, color: 'bg-gray-500' },
    { key: 'low', label: 'Niedrig', count: priorities['low'] || 0, color: 'bg-blue-300' },
  ]
})

const itemPriorities = computed(() => {
  if (!stats.value) return []
  const priorities = stats.value.items.by_priority
  return [
    { key: 'high', label: 'Hoch', count: priorities['high'] || 0, color: 'bg-red-500' },
    { key: 'medium', label: 'Mittel', count: priorities['medium'] || 0, color: 'bg-yellow-500' },
    { key: 'low', label: 'Niedrig', count: priorities['low'] || 0, color: 'bg-blue-500' },
    { key: 'none', label: 'Keine', count: priorities['none'] || 0, color: 'bg-gray-300' },
  ]
})

const llmWorkerStats = computed(() => {
  if (!stats.value) return null
  const s = stats.value.llm_worker.stats as Record<string, number | string | null>
  const totalTime = (s.total_processing_time as number) || 0
  const itemsTimed = (s.items_timed as number) || 0
  const meanTime = itemsTimed > 0 ? totalTime / itemsTimed : 0

  return {
    fresh: s.fresh_processed || 0,
    backlog: s.backlog_processed || 0,
    errors: s.errors || 0,
    lastProcessed: s.last_processed_at
      ? new Date(s.last_processed_at as string).toLocaleTimeString('de-DE')
      : '-',
    meanTime,
    itemsTimed
  }
})

const estimatedTimeRemaining = computed(() => {
  if (!stats.value || !llmWorkerStats.value) return null
  const meanTime = llmWorkerStats.value.meanTime
  if (meanTime <= 0) return null

  // Only count high, edge_case, unknown (not low, which is skipped)
  const queue = stats.value.processing_queue.by_retry_priority
  const itemsToProcess = (queue['high'] || 0) + (queue['edge_case'] || 0) + (queue['unknown'] || 0)
  if (itemsToProcess === 0) return null

  const totalSeconds = itemsToProcess * meanTime
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)

  if (hours > 0) {
    return `~${hours}h ${minutes}m`
  }
  return `~${minutes}m`
})

onMounted(() => {
  fetchStats()
  startAutoRefresh()
})

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<template>
  <div class="space-y-6">
    <!-- Header -->
    <div class="flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-bold text-gray-900">System Status</h1>
        <p class="text-sm text-gray-500">
          Scheduler, Worker und Verarbeitungs-Queue
        </p>
      </div>
      <div class="flex items-center gap-3">
        <label class="flex items-center gap-2 text-sm text-gray-600">
          <input
            type="checkbox"
            :checked="autoRefresh"
            class="rounded border-gray-300"
            @change="toggleAutoRefresh"
          />
          Auto ({{ refreshInterval }}s)
        </label>
        <button
          type="button"
          class="btn btn-secondary"
          :disabled="loading"
          @click="fetchStats"
        >
          <ArrowPathIcon class="h-4 w-4" :class="{ 'animate-spin': loading }" />
          Aktualisieren
        </button>
      </div>
    </div>

    <!-- Error message -->
    <div v-if="error" class="rounded-lg bg-red-50 p-4 text-red-700">
      <ExclamationTriangleIcon class="mr-2 inline h-5 w-5" />
      {{ error }}
    </div>

    <!-- Loading -->
    <div v-if="loading && !stats" class="py-12 text-center text-gray-500">
      <ArrowPathIcon class="mx-auto h-8 w-8 animate-spin" />
      <p class="mt-2">Lade Statistiken...</p>
    </div>

    <template v-else-if="stats">
      <!-- Worker Status Cards -->
      <div class="grid gap-4 md:grid-cols-3">
        <!-- Scheduler -->
        <div class="card">
          <div class="flex items-center justify-between">
            <h2 class="font-medium text-gray-900">Scheduler</h2>
            <span
              class="flex items-center gap-1 text-sm"
              :class="stats.scheduler.running ? 'text-green-600' : 'text-red-600'"
            >
              <component
                :is="stats.scheduler.running ? CheckCircleIcon : XCircleIcon"
                class="h-4 w-4"
              />
              {{ stats.scheduler.running ? 'Läuft' : 'Gestoppt' }}
            </span>
          </div>

          <div v-if="nextSchedulerRun" class="mt-2 flex items-center gap-1 text-sm text-gray-500">
            <ClockIcon class="h-4 w-4" />
            Nächster Fetch: {{ nextSchedulerRun }}
          </div>

          <div class="mt-4 flex gap-2">
            <button
              v-if="!stats.scheduler.running"
              type="button"
              class="btn btn-sm btn-primary"
              :disabled="actionLoading === 'scheduler-start'"
              @click="controlAction(adminApi.startScheduler, 'scheduler-start')"
            >
              <PlayIcon class="h-4 w-4" />
              Start
            </button>
            <button
              v-else
              type="button"
              class="btn btn-sm btn-secondary"
              :disabled="actionLoading === 'scheduler-stop'"
              @click="controlAction(adminApi.stopScheduler, 'scheduler-stop')"
            >
              <StopIcon class="h-4 w-4" />
              Stop
            </button>
          </div>
        </div>

        <!-- LLM Worker -->
        <div class="card">
          <div class="flex items-center justify-between">
            <h2 class="font-medium text-gray-900">LLM Worker</h2>
            <span
              class="flex items-center gap-1 text-sm"
              :class="{
                'text-green-600': stats.llm_worker.running && !stats.llm_worker.paused,
                'text-yellow-600': stats.llm_worker.paused,
                'text-red-600': !stats.llm_worker.running
              }"
            >
              <component
                :is="stats.llm_worker.running ? (stats.llm_worker.paused ? PauseIcon : CheckCircleIcon) : XCircleIcon"
                class="h-4 w-4"
              />
              {{ !stats.llm_worker.running ? 'Gestoppt' : stats.llm_worker.paused ? 'Pausiert' : 'Läuft' }}
            </span>
          </div>

          <div v-if="llmWorkerStats" class="mt-2 space-y-1 text-sm text-gray-500">
            <div>Fresh: {{ llmWorkerStats.fresh }} | Backlog: {{ llmWorkerStats.backlog }}</div>
            <div>Fehler: {{ llmWorkerStats.errors }} | Zuletzt: {{ llmWorkerStats.lastProcessed }}</div>
            <div v-if="llmWorkerStats.itemsTimed > 0">
              Durchschnitt: {{ llmWorkerStats.meanTime.toFixed(1) }}s/Nachricht
              <span class="text-gray-400">({{ llmWorkerStats.itemsTimed }} gemessen)</span>
            </div>
          </div>

          <div class="mt-4 flex gap-2">
            <button
              v-if="!stats.llm_worker.running"
              type="button"
              class="btn btn-sm btn-primary"
              :disabled="actionLoading === 'llm-start'"
              @click="controlAction(adminApi.startLlmWorker, 'llm-start')"
            >
              <PlayIcon class="h-4 w-4" />
              Start
            </button>
            <template v-else>
              <button
                v-if="!stats.llm_worker.paused"
                type="button"
                class="btn btn-sm btn-secondary"
                :disabled="actionLoading === 'llm-pause'"
                @click="controlAction(adminApi.pauseLlmWorker, 'llm-pause')"
              >
                <PauseIcon class="h-4 w-4" />
                Pause
              </button>
              <button
                v-else
                type="button"
                class="btn btn-sm btn-primary"
                :disabled="actionLoading === 'llm-resume'"
                @click="controlAction(adminApi.resumeLlmWorker, 'llm-resume')"
              >
                <PlayIcon class="h-4 w-4" />
                Fortsetzen
              </button>
              <button
                type="button"
                class="btn btn-sm btn-secondary"
                :disabled="actionLoading === 'llm-stop'"
                @click="controlAction(adminApi.stopLlmWorker, 'llm-stop')"
              >
                <StopIcon class="h-4 w-4" />
                Stop
              </button>
            </template>
          </div>
        </div>

        <!-- Classifier Worker -->
        <div class="card">
          <div class="flex items-center justify-between">
            <h2 class="font-medium text-gray-900">Classifier</h2>
            <span
              class="flex items-center gap-1 text-sm"
              :class="{
                'text-green-600': stats.classifier_worker.running && !stats.classifier_worker.paused,
                'text-yellow-600': stats.classifier_worker.paused,
                'text-red-600': !stats.classifier_worker.running
              }"
            >
              <component
                :is="stats.classifier_worker.running ? (stats.classifier_worker.paused ? PauseIcon : CheckCircleIcon) : XCircleIcon"
                class="h-4 w-4"
              />
              {{ !stats.classifier_worker.running ? 'Gestoppt' : stats.classifier_worker.paused ? 'Pausiert' : 'Läuft' }}
            </span>
          </div>

          <div class="mt-2 text-sm text-gray-500">
            Warten auf Klassifizierung: {{ stats.processing_queue.awaiting_classifier }}
          </div>

          <div class="mt-4 flex gap-2">
            <button
              v-if="!stats.classifier_worker.running"
              type="button"
              class="btn btn-sm btn-primary"
              :disabled="actionLoading === 'clf-start'"
              @click="controlAction(adminApi.startClassifierWorker, 'clf-start')"
            >
              <PlayIcon class="h-4 w-4" />
              Start
            </button>
            <template v-else>
              <button
                v-if="!stats.classifier_worker.paused"
                type="button"
                class="btn btn-sm btn-secondary"
                :disabled="actionLoading === 'clf-pause'"
                @click="controlAction(adminApi.pauseClassifierWorker, 'clf-pause')"
              >
                <PauseIcon class="h-4 w-4" />
                Pause
              </button>
              <button
                v-else
                type="button"
                class="btn btn-sm btn-primary"
                :disabled="actionLoading === 'clf-resume'"
                @click="controlAction(adminApi.resumeClassifierWorker, 'clf-resume')"
              >
                <PlayIcon class="h-4 w-4" />
                Fortsetzen
              </button>
              <button
                type="button"
                class="btn btn-sm btn-secondary"
                :disabled="actionLoading === 'clf-stop'"
                @click="controlAction(adminApi.stopClassifierWorker, 'clf-stop')"
              >
                <StopIcon class="h-4 w-4" />
                Stop
              </button>
            </template>
          </div>
        </div>
      </div>

      <!-- Processing Queue -->
      <div class="card">
        <div class="flex items-center justify-between">
          <div>
            <h2 class="font-medium text-gray-900">Verarbeitungs-Queue</h2>
            <p class="text-sm text-gray-500">
              {{ stats.processing_queue.total }} Nachrichten warten auf LLM-Verarbeitung
            </p>
          </div>
          <div v-if="estimatedTimeRemaining" class="text-right">
            <div class="text-sm text-gray-500">Geschätzte Restzeit</div>
            <div class="text-lg font-semibold text-gray-900">{{ estimatedTimeRemaining }}</div>
          </div>
        </div>

        <div class="mt-4 grid grid-cols-4 gap-4">
          <div
            v-for="p in queuePriorities"
            :key="p.key"
            class="rounded-lg border border-gray-200 p-3 text-center"
          >
            <div class="text-2xl font-bold text-gray-900">{{ p.count }}</div>
            <div class="mt-1 flex items-center justify-center gap-1 text-sm text-gray-500">
              <span class="h-2 w-2 rounded-full" :class="p.color"></span>
              {{ p.label }}
            </div>
          </div>
        </div>
      </div>

      <!-- Item Stats -->
      <div class="card">
        <h2 class="font-medium text-gray-900">Nachrichten</h2>
        <p class="text-sm text-gray-500">
          {{ stats.items.total.toLocaleString('de-DE') }} Nachrichten insgesamt
          ({{ stats.items.unread }} ungelesen, {{ stats.items.starred }} markiert)
        </p>

        <div class="mt-4">
          <!-- Priority bar -->
          <div class="flex h-6 overflow-hidden rounded-full bg-gray-100">
            <div
              v-for="p in itemPriorities"
              :key="p.key"
              :class="p.color"
              :style="{ width: stats.items.total ? `${(p.count / stats.items.total) * 100}%` : '0%' }"
              :title="`${p.label}: ${p.count}`"
            ></div>
          </div>

          <!-- Legend -->
          <div class="mt-2 flex flex-wrap gap-4 text-sm">
            <div v-for="p in itemPriorities" :key="p.key" class="flex items-center gap-1">
              <span class="h-3 w-3 rounded" :class="p.color"></span>
              <span class="text-gray-600">{{ p.label }}: {{ p.count.toLocaleString('de-DE') }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Last updated -->
      <div class="text-right text-sm text-gray-400">
        Zuletzt aktualisiert: {{ new Date(stats.timestamp).toLocaleString('de-DE') }}
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import {
  ArrowPathIcon,
  PlayIcon,
  StopIcon,
  PauseIcon,
  CheckCircleIcon,
  XCircleIcon,
  ClockIcon,
  ExclamationTriangleIcon,
  ServerIcon,
  CpuChipIcon,
  CircleStackIcon,
  UserIcon,
  BoltIcon,
  MoonIcon,
  SunIcon,
  DocumentTextIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  FunnelIcon
} from '@heroicons/vue/24/outline'
import {
  adminApi,
  type SystemStatsResponse,
  type GPU1Status,
  type HealthCheckResponse,
  type LogsResponse,
  type LogEntry
} from '@/api'

// State
const stats = ref<SystemStatsResponse | null>(null)
const health = ref<HealthCheckResponse | null>(null)
const gpu1Status = ref<GPU1Status | null>(null)
const logs = ref<LogsResponse | null>(null)

const loading = ref(true)
const error = ref<string | null>(null)
const actionLoading = ref<string | null>(null)
const autoRefresh = ref(true)
const refreshInterval = ref(60) // 1 minute
let refreshTimer: number | null = null

// Logs pagination and filters
const logsPage = ref(1)
const logsPageSize = ref(100)
const logsLevelFilter = ref<string | null>(null)
const logsSearch = ref('')

// Active tab
const activeTab = ref<'status' | 'logs'>('status')

// Fetch functions
const fetchStats = async () => {
  try {
    const response = await adminApi.getStats()
    stats.value = response.data
  } catch (e) {
    console.error('Failed to fetch stats:', e)
  }
}

const fetchHealth = async () => {
  try {
    const response = await adminApi.getHealth()
    health.value = response.data
  } catch (e) {
    console.error('Failed to fetch health:', e)
  }
}

const fetchGpu1Status = async () => {
  try {
    const response = await adminApi.getGpu1Status()
    gpu1Status.value = response.data
  } catch (e) {
    console.error('Failed to fetch GPU1 status:', e)
  }
}

const fetchLogs = async () => {
  try {
    const response = await adminApi.getLogs({
      page: logsPage.value,
      page_size: logsPageSize.value,
      level: logsLevelFilter.value || undefined,
      search: logsSearch.value || undefined
    })
    logs.value = response.data
  } catch (e) {
    console.error('Failed to fetch logs:', e)
  }
}

const fetchAll = async () => {
  error.value = null
  await Promise.all([fetchStats(), fetchHealth(), fetchGpu1Status(), fetchLogs()])
  loading.value = false
}

// Auto-refresh
const startAutoRefresh = () => {
  if (refreshTimer) clearInterval(refreshTimer)
  if (autoRefresh.value) {
    refreshTimer = window.setInterval(fetchAll, refreshInterval.value * 1000)
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

// Computed values
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
    { key: 'low', label: 'Niedrig', count: priorities['low'] || 0, color: 'bg-blue-300' }
  ]
})

const itemPriorities = computed(() => {
  if (!stats.value) return []
  const priorities = stats.value.items.by_priority
  return [
    { key: 'high', label: 'Hoch', count: priorities['high'] || 0, color: 'bg-red-500' },
    { key: 'medium', label: 'Mittel', count: priorities['medium'] || 0, color: 'bg-yellow-500' },
    { key: 'low', label: 'Niedrig', count: priorities['low'] || 0, color: 'bg-blue-500' },
    { key: 'none', label: 'Keine', count: priorities['none'] || 0, color: 'bg-gray-300' }
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

const gpu1IdleFormatted = computed(() => {
  if (!gpu1Status.value?.idle_time) return null
  const secs = Math.round(gpu1Status.value.idle_time)
  const mins = Math.floor(secs / 60)
  const remainingSecs = secs % 60
  return `${mins}m ${remainingSecs}s`
})

const gpu1ActiveHoursFormatted = computed(() => {
  if (!gpu1Status.value) return ''
  return `${gpu1Status.value.active_hours_start}:00 - ${gpu1Status.value.active_hours_end}:00`
})

// Log level colors
const logLevelClass = (level: string) => {
  switch (level) {
    case 'ERROR':
      return 'text-red-600 bg-red-50'
    case 'WARNING':
      return 'text-yellow-600 bg-yellow-50'
    case 'INFO':
      return 'text-blue-600 bg-blue-50'
    case 'DEBUG':
      return 'text-gray-500 bg-gray-50'
    default:
      return 'text-gray-600 bg-gray-50'
  }
}

// Watch for log filter changes
watch([logsLevelFilter, logsSearch], () => {
  logsPage.value = 1
  fetchLogs()
})

watch(logsPage, () => {
  fetchLogs()
})

onMounted(() => {
  fetchAll()
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
          Dienste, GPU1 Power Management und Logs
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
          @click="fetchAll"
        >
          <ArrowPathIcon class="h-4 w-4" :class="{ 'animate-spin': loading }" />
          Aktualisieren
        </button>
      </div>
    </div>

    <!-- Tabs -->
    <div class="border-b border-gray-200">
      <nav class="-mb-px flex space-x-8">
        <button
          type="button"
          class="border-b-2 px-1 py-2 text-sm font-medium"
          :class="activeTab === 'status' ? 'border-blue-500 text-blue-600' : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700'"
          @click="activeTab = 'status'"
        >
          <ServerIcon class="mr-1 inline h-4 w-4" />
          Status
        </button>
        <button
          type="button"
          class="border-b-2 px-1 py-2 text-sm font-medium"
          :class="activeTab === 'logs' ? 'border-blue-500 text-blue-600' : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700'"
          @click="activeTab = 'logs'"
        >
          <DocumentTextIcon class="mr-1 inline h-4 w-4" />
          Logs
          <span v-if="logs" class="ml-1 rounded-full bg-gray-100 px-2 py-0.5 text-xs">
            {{ logs.total }}
          </span>
        </button>
      </nav>
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

    <!-- Status Tab -->
    <template v-else-if="activeTab === 'status'">
      <!-- Service Availability Cards -->
      <div class="grid gap-4 md:grid-cols-4">
        <!-- Database -->
        <div class="card">
          <div class="flex items-center gap-3">
            <CircleStackIcon class="h-8 w-8 text-gray-400" />
            <div>
              <div class="text-sm font-medium text-gray-500">Datenbank</div>
              <div class="flex items-center gap-1">
                <component
                  :is="health?.database_ok ? CheckCircleIcon : XCircleIcon"
                  class="h-5 w-5"
                  :class="health?.database_ok ? 'text-green-500' : 'text-red-500'"
                />
                <span :class="health?.database_ok ? 'text-green-600' : 'text-red-600'">
                  {{ health?.database_ok ? 'Verbunden' : 'Fehler' }}
                </span>
              </div>
            </div>
          </div>
        </div>

        <!-- Ollama/LLM -->
        <div class="card">
          <div class="flex items-center gap-3">
            <CpuChipIcon class="h-8 w-8 text-gray-400" />
            <div>
              <div class="text-sm font-medium text-gray-500">Ollama (LLM)</div>
              <div class="flex items-center gap-1">
                <component
                  :is="health?.llm_available ? CheckCircleIcon : XCircleIcon"
                  class="h-5 w-5"
                  :class="health?.llm_available ? 'text-green-500' : 'text-red-500'"
                />
                <span :class="health?.llm_available ? 'text-green-600' : 'text-red-600'">
                  {{ health?.llm_available ? 'Verfügbar' : 'Offline' }}
                </span>
              </div>
            </div>
          </div>
        </div>

        <!-- GPU1 Availability -->
        <div class="card">
          <div class="flex items-center gap-3">
            <BoltIcon class="h-8 w-8 text-gray-400" />
            <div>
              <div class="text-sm font-medium text-gray-500">GPU1</div>
              <div class="flex items-center gap-1">
                <component
                  :is="gpu1Status?.available ? CheckCircleIcon : XCircleIcon"
                  class="h-5 w-5"
                  :class="gpu1Status?.available ? 'text-green-500' : 'text-red-500'"
                />
                <span :class="gpu1Status?.available ? 'text-green-600' : 'text-red-600'">
                  {{ gpu1Status?.available ? 'Läuft' : 'Aus' }}
                </span>
              </div>
            </div>
          </div>
        </div>

        <!-- Overall Health -->
        <div class="card">
          <div class="flex items-center gap-3">
            <ServerIcon class="h-8 w-8 text-gray-400" />
            <div>
              <div class="text-sm font-medium text-gray-500">System</div>
              <div class="flex items-center gap-1">
                <component
                  :is="health?.status === 'healthy' ? CheckCircleIcon : ExclamationTriangleIcon"
                  class="h-5 w-5"
                  :class="{
                    'text-green-500': health?.status === 'healthy',
                    'text-yellow-500': health?.status === 'degraded',
                    'text-red-500': health?.status === 'unhealthy'
                  }"
                />
                <span :class="{
                  'text-green-600': health?.status === 'healthy',
                  'text-yellow-600': health?.status === 'degraded',
                  'text-red-600': health?.status === 'unhealthy'
                }">
                  {{ health?.status === 'healthy' ? 'Gesund' : health?.status === 'degraded' ? 'Eingeschränkt' : 'Fehler' }}
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- GPU1 Power Management -->
      <div v-if="gpu1Status?.enabled" class="card">
        <div class="flex items-center justify-between">
          <h2 class="font-medium text-gray-900">
            <BoltIcon class="mr-1 inline h-5 w-5" />
            GPU1 Power Management
          </h2>
          <span
            class="flex items-center gap-1 rounded-full px-2 py-1 text-xs font-medium"
            :class="gpu1Status.available ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'"
          >
            <component :is="gpu1Status.available ? SunIcon : MoonIcon" class="h-4 w-4" />
            {{ gpu1Status.available ? 'Wach' : 'Schläft' }}
          </span>
        </div>

        <div class="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <!-- WoL Status -->
          <div class="rounded-lg border border-gray-200 p-3">
            <div class="text-xs font-medium text-gray-500">Wake-on-LAN</div>
            <div class="mt-1 flex items-center gap-1">
              <component
                :is="gpu1Status.was_sleeping ? CheckCircleIcon : XCircleIcon"
                class="h-4 w-4"
                :class="gpu1Status.was_sleeping ? 'text-blue-500' : 'text-gray-400'"
              />
              <span class="text-sm">
                {{ gpu1Status.was_sleeping ? 'Von uns geweckt' : 'War bereits wach' }}
              </span>
            </div>
            <div v-if="gpu1Status.wake_time" class="mt-1 text-xs text-gray-400">
              {{ new Date(gpu1Status.wake_time).toLocaleString('de-DE') }}
            </div>
          </div>

          <!-- Active Hours -->
          <div class="rounded-lg border border-gray-200 p-3">
            <div class="text-xs font-medium text-gray-500">Aktive Stunden</div>
            <div class="mt-1 flex items-center gap-1">
              <ClockIcon class="h-4 w-4 text-gray-400" />
              <span class="text-sm">{{ gpu1ActiveHoursFormatted }}</span>
            </div>
            <div class="mt-1 text-xs" :class="gpu1Status.within_active_hours ? 'text-green-600' : 'text-gray-400'">
              {{ gpu1Status.within_active_hours ? 'Aktiv (WoL erlaubt)' : 'Inaktiv (kein WoL)' }}
            </div>
          </div>

          <!-- Idle Time -->
          <div class="rounded-lg border border-gray-200 p-3">
            <div class="text-xs font-medium text-gray-500">Leerlauf</div>
            <div class="mt-1 text-sm">
              {{ gpu1IdleFormatted || '-' }}
            </div>
            <div class="mt-1 text-xs text-gray-400">
              Auto-Shutdown nach {{ gpu1Status.idle_timeout }}s
            </div>
          </div>

          <!-- Logged-in Users -->
          <div class="rounded-lg border border-gray-200 p-3">
            <div class="text-xs font-medium text-gray-500">Angemeldete Benutzer</div>
            <div class="mt-1 flex items-center gap-1">
              <UserIcon class="h-4 w-4 text-gray-400" />
              <span class="text-sm">
                {{ gpu1Status.logged_in_users.length === 0 ? 'Keine' : gpu1Status.logged_in_users.join(', ') }}
              </span>
            </div>
            <div v-if="gpu1Status.logged_in_users.length > 0" class="mt-1 text-xs text-yellow-600">
              Shutdown blockiert
            </div>
          </div>
        </div>

        <!-- Pending Shutdown Warning -->
        <div
          v-if="gpu1Status.pending_shutdown"
          class="mt-4 flex items-center gap-2 rounded-lg bg-yellow-50 p-3 text-sm text-yellow-700"
        >
          <ExclamationTriangleIcon class="h-5 w-5" />
          Auto-Shutdown steht bevor (Leerlauf überschritten, keine Benutzer)
        </div>

        <!-- Config Info -->
        <div class="mt-4 text-xs text-gray-400">
          MAC: {{ gpu1Status.mac_address }} | SSH: {{ gpu1Status.ssh_host }}
        </div>
      </div>

      <!-- Worker Status Cards -->
      <div v-if="stats" class="grid gap-4 md:grid-cols-3">
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
            <div>Warteschlange: {{ stats.processing_queue.total }} | Verarbeitet: {{ Number(llmWorkerStats.fresh) + Number(llmWorkerStats.backlog) }}</div>
            <div>Fehler: {{ llmWorkerStats.errors }} | Zuletzt: {{ llmWorkerStats.lastProcessed }}</div>
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

          <div class="mt-2 space-y-1 text-sm text-gray-500">
            <div>Warteschlange: {{ stats.processing_queue.awaiting_classifier }}</div>
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
      <div v-if="stats" class="card">
        <h2 class="font-medium text-gray-900">Verarbeitungs-Queue</h2>
        <p class="text-sm text-gray-500">
          {{ stats.processing_queue.total }} Nachrichten warten auf LLM-Verarbeitung
        </p>

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
      <div v-if="stats" class="card">
        <h2 class="font-medium text-gray-900">Nachrichten</h2>
        <p class="text-sm text-gray-500">
          {{ stats.items.total.toLocaleString('de-DE') }} Nachrichten insgesamt
        </p>

        <div class="mt-4">
          <div class="flex h-6 overflow-hidden rounded-full bg-gray-100">
            <div
              v-for="p in itemPriorities"
              :key="p.key"
              :class="p.color"
              :style="{ width: stats.items.total ? `${(p.count / stats.items.total) * 100}%` : '0%' }"
              :title="`${p.label}: ${p.count}`"
            ></div>
          </div>

          <div class="mt-2 flex flex-wrap gap-4 text-sm">
            <div v-for="p in itemPriorities" :key="p.key" class="flex items-center gap-1">
              <span class="h-3 w-3 rounded" :class="p.color"></span>
              <span class="text-gray-600">{{ p.label }}: {{ p.count.toLocaleString('de-DE') }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Last updated -->
      <div v-if="stats" class="text-right text-sm text-gray-400">
        Zuletzt aktualisiert: {{ new Date(stats.timestamp).toLocaleString('de-DE') }}
      </div>
    </template>

    <!-- Logs Tab -->
    <template v-else-if="activeTab === 'logs'">
      <!-- Filters -->
      <div class="card">
        <div class="flex flex-wrap items-center gap-4">
          <div class="flex items-center gap-2">
            <FunnelIcon class="h-4 w-4 text-gray-400" />
            <select
              v-model="logsLevelFilter"
              class="rounded border-gray-300 text-sm"
            >
              <option :value="null">Alle Level</option>
              <option value="ERROR">ERROR</option>
              <option value="WARNING">WARNING</option>
              <option value="INFO">INFO</option>
              <option value="DEBUG">DEBUG</option>
            </select>
          </div>

          <input
            v-model="logsSearch"
            type="text"
            placeholder="Suchen..."
            class="rounded border-gray-300 text-sm"
            @keyup.enter="fetchLogs"
          />

          <div class="ml-auto text-sm text-gray-500">
            {{ logs?.total || 0 }} Einträge (max 1000)
          </div>
        </div>
      </div>

      <!-- Log Entries -->
      <div class="card overflow-hidden p-0">
        <div class="max-h-[600px] overflow-auto">
          <table class="w-full text-xs">
            <thead class="sticky top-0 bg-gray-50">
              <tr>
                <th class="px-3 py-2 text-left font-medium text-gray-500">Zeit</th>
                <th class="px-3 py-2 text-left font-medium text-gray-500">Level</th>
                <th class="px-3 py-2 text-left font-medium text-gray-500">Logger</th>
                <th class="px-3 py-2 text-left font-medium text-gray-500">Nachricht</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-100">
              <tr
                v-for="(entry, idx) in logs?.entries"
                :key="idx"
                class="hover:bg-gray-50"
              >
                <td class="whitespace-nowrap px-3 py-1.5 text-gray-500">
                  {{ new Date(entry.timestamp).toLocaleTimeString('de-DE') }}
                </td>
                <td class="px-3 py-1.5">
                  <span
                    class="inline-block rounded px-1.5 py-0.5 text-xs font-medium"
                    :class="logLevelClass(entry.level)"
                  >
                    {{ entry.level }}
                  </span>
                </td>
                <td class="whitespace-nowrap px-3 py-1.5 text-gray-600">
                  {{ entry.logger.split('.').slice(-2).join('.') }}
                </td>
                <td class="max-w-xl truncate px-3 py-1.5 font-mono text-gray-900">
                  {{ entry.message }}
                </td>
              </tr>
              <tr v-if="!logs?.entries.length">
                <td colspan="4" class="px-3 py-8 text-center text-gray-500">
                  Keine Log-Einträge gefunden
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Pagination -->
      <div v-if="logs && logs.total_pages > 1" class="flex items-center justify-between">
        <div class="text-sm text-gray-500">
          Seite {{ logs.page }} von {{ logs.total_pages }}
        </div>
        <div class="flex gap-2">
          <button
            type="button"
            class="btn btn-sm btn-secondary"
            :disabled="logsPage <= 1"
            @click="logsPage--"
          >
            <ChevronLeftIcon class="h-4 w-4" />
            Zurück
          </button>
          <button
            type="button"
            class="btn btn-sm btn-secondary"
            :disabled="logsPage >= logs.total_pages"
            @click="logsPage++"
          >
            Weiter
            <ChevronRightIcon class="h-4 w-4" />
          </button>
        </div>
      </div>
    </template>
  </div>
</template>

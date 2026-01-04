<script setup lang="ts">
import { ref, onMounted } from 'vue'
import {
  CheckIcon,
  EnvelopeIcon,
  EyeIcon,
  PaperAirplaneIcon,
  ExclamationCircleIcon
} from '@heroicons/vue/24/outline'
import { emailApi, llmApi, type PreviewBriefingResponse, type OllamaModel } from '@/api'

const saved = ref(false)
const loadingModels = ref(false)
const ollamaAvailable = ref(false)
const ollamaModels = ref<OllamaModel[]>([])

const settings = ref({
  llm: {
    provider: 'ollama',
    ollama_url: 'http://localhost:11434',
    ollama_model: 'llama3.2',
    openrouter_key: '',
    openrouter_model: 'mistralai/mistral-7b-instruct:free'
  },
  notifications: {
    email_enabled: false,
    email_recipients: '',
    notify_critical: true,
    notify_high: false
  },
  scheduler: {
    enabled: true,
    default_interval: 60
  }
})

// Email export state
const emailExport = ref({
  recipients: '',
  min_priority: 'low',
  hours_back: 24,
  include_read: false
})

const emailStatus = ref<{ type: 'success' | 'error'; message: string } | null>(null)
const sending = ref(false)
const previewing = ref(false)
const preview = ref<PreviewBriefingResponse | null>(null)
const showPreview = ref(false)

const testEmailAddress = ref('')
const testEmailStatus = ref<{ type: 'success' | 'error'; message: string } | null>(null)
const testingSend = ref(false)

const priorities = [
  { value: 'critical', label: 'Nur Kritisch' },
  { value: 'high', label: 'Hoch und höher' },
  { value: 'medium', label: 'Mittel und höher' },
  { value: 'low', label: 'Alle Prioritäten' }
]

const saveSettings = async () => {
  // TODO: Implement settings API
  saved.value = true
  setTimeout(() => {
    saved.value = false
  }, 2000)
}

const previewBriefing = async () => {
  previewing.value = true
  emailStatus.value = null
  try {
    const response = await emailApi.previewBriefing({
      min_priority: emailExport.value.min_priority,
      hours_back: emailExport.value.hours_back,
      include_read: emailExport.value.include_read
    })
    preview.value = response.data
    showPreview.value = true
  } catch (e) {
    emailStatus.value = {
      type: 'error',
      message: e instanceof Error ? e.message : 'Vorschau fehlgeschlagen'
    }
  } finally {
    previewing.value = false
  }
}

const sendBriefing = async () => {
  const recipientList = emailExport.value.recipients
    .split(',')
    .map(r => r.trim())
    .filter(r => r.length > 0)

  if (recipientList.length === 0) {
    emailStatus.value = { type: 'error', message: 'Bitte Empfänger angeben' }
    return
  }

  sending.value = true
  emailStatus.value = null
  try {
    const response = await emailApi.sendBriefing({
      recipients: recipientList,
      min_priority: emailExport.value.min_priority,
      hours_back: emailExport.value.hours_back,
      include_read: emailExport.value.include_read
    })
    emailStatus.value = {
      type: response.data.success ? 'success' : 'error',
      message: response.data.message
    }
  } catch (e) {
    emailStatus.value = {
      type: 'error',
      message: e instanceof Error ? e.message : 'Senden fehlgeschlagen'
    }
  } finally {
    sending.value = false
  }
}

const sendTestEmail = async () => {
  if (!testEmailAddress.value) {
    testEmailStatus.value = { type: 'error', message: 'Bitte E-Mail-Adresse angeben' }
    return
  }

  testingSend.value = true
  testEmailStatus.value = null
  try {
    const response = await emailApi.testEmail(testEmailAddress.value)
    testEmailStatus.value = {
      type: response.data.success ? 'success' : 'error',
      message: response.data.message
    }
  } catch (e: unknown) {
    const errorMessage = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      || (e instanceof Error ? e.message : 'Test fehlgeschlagen')
    testEmailStatus.value = { type: 'error', message: errorMessage }
  } finally {
    testingSend.value = false
  }
}

const loadOllamaModels = async () => {
  loadingModels.value = true
  try {
    const response = await llmApi.getModels()
    ollamaAvailable.value = response.data.available
    ollamaModels.value = response.data.models
    settings.value.llm.ollama_url = response.data.base_url
    settings.value.llm.ollama_model = response.data.current_model
  } catch (e) {
    console.error('Failed to load Ollama models:', e)
    ollamaAvailable.value = false
  } finally {
    loadingModels.value = false
  }
}

onMounted(() => {
  loadOllamaModels()
})
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-gray-900">Einstellungen</h1>
      <p class="text-sm text-gray-500">Konfiguration des News-Aggregators</p>
    </div>

    <!-- Email Export -->
    <div class="card">
      <h2 class="text-lg font-medium text-gray-900">
        <EnvelopeIcon class="mr-2 inline h-5 w-5" />
        E-Mail Briefing
      </h2>
      <p class="mt-1 text-sm text-gray-500">
        Briefing per E-Mail versenden
      </p>

      <div class="mt-4 space-y-4">
        <div>
          <label class="block text-sm font-medium text-gray-700">
            Empfänger (komma-getrennt)
          </label>
          <input
            v-model="emailExport.recipients"
            type="text"
            class="input mt-1"
            placeholder="user@example.com, other@example.com"
          />
        </div>

        <div class="grid gap-4 sm:grid-cols-3">
          <div>
            <label class="block text-sm font-medium text-gray-700">
              Mindest-Priorität
            </label>
            <select v-model="emailExport.min_priority" class="input mt-1">
              <option v-for="p in priorities" :key="p.value" :value="p.value">
                {{ p.label }}
              </option>
            </select>
          </div>
          <div>
            <label class="block text-sm font-medium text-gray-700">
              Zeitraum (Stunden)
            </label>
            <input
              v-model.number="emailExport.hours_back"
              type="number"
              min="1"
              max="168"
              class="input mt-1"
            />
          </div>
          <div class="flex items-end pb-2">
            <label class="flex items-center gap-2 text-sm text-gray-700">
              <input
                v-model="emailExport.include_read"
                type="checkbox"
                class="rounded border-gray-300"
              />
              Gelesene einbeziehen
            </label>
          </div>
        </div>

        <!-- Status message -->
        <div
          v-if="emailStatus"
          class="rounded-lg p-3 text-sm"
          :class="emailStatus.type === 'success' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'"
        >
          <component
            :is="emailStatus.type === 'success' ? CheckIcon : ExclamationCircleIcon"
            class="mr-2 inline h-4 w-4"
          />
          {{ emailStatus.message }}
        </div>

        <div class="flex gap-3">
          <button
            type="button"
            class="btn btn-secondary"
            :disabled="previewing"
            @click="previewBriefing"
          >
            <EyeIcon class="mr-2 h-4 w-4" />
            {{ previewing ? 'Lade...' : 'Vorschau' }}
          </button>
          <button
            type="button"
            class="btn btn-primary"
            :disabled="sending || !emailExport.recipients"
            @click="sendBriefing"
          >
            <PaperAirplaneIcon class="mr-2 h-4 w-4" />
            {{ sending ? 'Sende...' : 'Briefing senden' }}
          </button>
        </div>
      </div>

      <!-- Preview Modal -->
      <div
        v-if="showPreview && preview"
        class="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50 p-4"
        @click.self="showPreview = false"
      >
        <div class="max-h-[80vh] w-full max-w-3xl overflow-hidden rounded-lg bg-white shadow-xl">
          <div class="flex items-center justify-between border-b border-gray-200 p-4">
            <div>
              <h3 class="font-medium text-gray-900">{{ preview.subject }}</h3>
              <p class="text-sm text-gray-500">{{ preview.items_count }} Nachrichten</p>
            </div>
            <button
              type="button"
              class="text-gray-400 hover:text-gray-600"
              @click="showPreview = false"
            >
              &times;
            </button>
          </div>
          <div class="max-h-[60vh] overflow-y-auto p-4">
            <div v-html="preview.html_body" />
          </div>
          <div class="border-t border-gray-200 p-4">
            <button
              type="button"
              class="btn btn-secondary"
              @click="showPreview = false"
            >
              Schließen
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Test Email -->
    <div class="card">
      <h2 class="text-lg font-medium text-gray-900">E-Mail Test</h2>
      <p class="mt-1 text-sm text-gray-500">
        Überprüfe die E-Mail-Konfiguration
      </p>

      <div class="mt-4 flex gap-3">
        <input
          v-model="testEmailAddress"
          type="email"
          class="input flex-1"
          placeholder="test@example.com"
        />
        <button
          type="button"
          class="btn btn-secondary"
          :disabled="testingSend"
          @click="sendTestEmail"
        >
          {{ testingSend ? 'Sende...' : 'Test senden' }}
        </button>
      </div>

      <div
        v-if="testEmailStatus"
        class="mt-3 rounded-lg p-3 text-sm"
        :class="testEmailStatus.type === 'success' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'"
      >
        {{ testEmailStatus.message }}
      </div>
    </div>

    <!-- LLM Settings -->
    <div class="card">
      <h2 class="text-lg font-medium text-gray-900">LLM-Konfiguration</h2>
      <p class="mt-1 text-sm text-gray-500">
        Einstellungen für die Textanalyse und Zusammenfassung
      </p>

      <div class="mt-4 space-y-4">
        <div>
          <label class="block text-sm font-medium text-gray-700">
            Primärer Provider
          </label>
          <select v-model="settings.llm.provider" class="input mt-1">
            <option value="ollama">Ollama (Lokal)</option>
            <option value="openrouter">OpenRouter (Cloud)</option>
          </select>
        </div>

        <div v-if="settings.llm.provider === 'ollama'" class="grid gap-4 sm:grid-cols-2">
          <div>
            <label class="block text-sm font-medium text-gray-700">
              Ollama URL
            </label>
            <input
              v-model="settings.llm.ollama_url"
              type="url"
              class="input mt-1"
              placeholder="http://localhost:11434"
              disabled
            />
            <p class="mt-1 text-xs text-gray-500">
              {{ ollamaAvailable ? '✓ Verbunden' : '✗ Nicht erreichbar' }}
            </p>
          </div>
          <div>
            <label class="block text-sm font-medium text-gray-700">
              Modell
            </label>
            <div v-if="loadingModels" class="mt-1 text-sm text-gray-500">
              Lade Modelle...
            </div>
            <select
              v-else-if="ollamaModels.length > 0"
              v-model="settings.llm.ollama_model"
              class="input mt-1"
            >
              <option v-for="model in ollamaModels" :key="model.name" :value="model.name">
                {{ model.name }}{{ model.is_current ? ' (aktuell)' : '' }}
              </option>
            </select>
            <input
              v-else
              v-model="settings.llm.ollama_model"
              type="text"
              class="input mt-1"
              placeholder="llama3.2"
            />
            <p v-if="ollamaModels.length > 0" class="mt-1 text-xs text-gray-500">
              {{ ollamaModels.length }} Modelle verfügbar
            </p>
          </div>
        </div>

        <div v-else class="grid gap-4 sm:grid-cols-2">
          <div>
            <label class="block text-sm font-medium text-gray-700">
              API-Key
            </label>
            <input
              v-model="settings.llm.openrouter_key"
              type="password"
              class="input mt-1"
              placeholder="sk-or-..."
            />
          </div>
          <div>
            <label class="block text-sm font-medium text-gray-700">
              Modell
            </label>
            <input
              v-model="settings.llm.openrouter_model"
              type="text"
              class="input mt-1"
              placeholder="mistralai/mistral-7b-instruct:free"
            />
          </div>
        </div>
      </div>
    </div>

    <!-- Scheduler Settings -->
    <div class="card">
      <h2 class="text-lg font-medium text-gray-900">Scheduler</h2>
      <p class="mt-1 text-sm text-gray-500">
        Automatischer Abruf der Quellen
      </p>

      <div class="mt-4 space-y-4">
        <div class="flex items-center gap-2">
          <input
            v-model="settings.scheduler.enabled"
            type="checkbox"
            class="rounded border-gray-300"
          />
          <label class="text-sm text-gray-700">
            Automatischen Abruf aktivieren
          </label>
        </div>

        <div v-if="settings.scheduler.enabled">
          <label class="block text-sm font-medium text-gray-700">
            Standard-Intervall (Minuten)
          </label>
          <input
            v-model.number="settings.scheduler.default_interval"
            type="number"
            min="5"
            max="1440"
            class="input mt-1 w-32"
          />
        </div>
      </div>
    </div>

    <!-- Save Button -->
    <div class="flex items-center justify-end gap-4">
      <span v-if="saved" class="flex items-center gap-1 text-sm text-green-600">
        <CheckIcon class="h-4 w-4" />
        Gespeichert
      </span>
      <button type="button" class="btn btn-primary" @click="saveSettings">
        Einstellungen speichern
      </button>
    </div>
  </div>
</template>

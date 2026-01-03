<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { CheckIcon } from '@heroicons/vue/24/outline'

const saved = ref(false)

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

const saveSettings = async () => {
  // TODO: Implement settings API
  saved.value = true
  setTimeout(() => {
    saved.value = false
  }, 2000)
}

onMounted(() => {
  // TODO: Load settings from API
})
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-gray-900">Einstellungen</h1>
      <p class="text-sm text-gray-500">Konfiguration des News-Aggregators</p>
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
            />
          </div>
          <div>
            <label class="block text-sm font-medium text-gray-700">
              Modell
            </label>
            <input
              v-model="settings.llm.ollama_model"
              type="text"
              class="input mt-1"
              placeholder="llama3.2"
            />
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

    <!-- Notification Settings -->
    <div class="card">
      <h2 class="text-lg font-medium text-gray-900">Benachrichtigungen</h2>
      <p class="mt-1 text-sm text-gray-500">
        E-Mail-Benachrichtigungen bei wichtigen Nachrichten
      </p>

      <div class="mt-4 space-y-4">
        <div class="flex items-center gap-2">
          <input
            v-model="settings.notifications.email_enabled"
            type="checkbox"
            class="rounded border-gray-300"
          />
          <label class="text-sm text-gray-700">
            E-Mail-Benachrichtigungen aktivieren
          </label>
        </div>

        <div v-if="settings.notifications.email_enabled">
          <label class="block text-sm font-medium text-gray-700">
            Empfänger (komma-getrennt)
          </label>
          <input
            v-model="settings.notifications.email_recipients"
            type="text"
            class="input mt-1"
            placeholder="user@example.com, other@example.com"
          />
        </div>

        <div class="flex gap-4">
          <div class="flex items-center gap-2">
            <input
              v-model="settings.notifications.notify_critical"
              type="checkbox"
              class="rounded border-gray-300"
            />
            <label class="text-sm text-gray-700">Bei kritischen Nachrichten</label>
          </div>
          <div class="flex items-center gap-2">
            <input
              v-model="settings.notifications.notify_high"
              type="checkbox"
              class="rounded border-gray-300"
            />
            <label class="text-sm text-gray-700">Bei hoher Priorität</label>
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

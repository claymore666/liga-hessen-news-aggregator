<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useSourcesStore } from '@/stores'
import { connectorsApi } from '@/api'
import { XMarkIcon } from '@heroicons/vue/24/outline'
import type { Source, Channel, ConnectorType, ConnectorInfo } from '@/types'

const props = defineProps<{
  source?: Source | null
  channel?: Channel | null
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

// Get URL from config based on connector type
const getUrlFromConfig = (config: Record<string, unknown>): string => {
  return (config?.url || config?.feed_url || config?.handle || config?.channel || '') as string
}

// Get follow_links setting from config (RSS only)
// Default to true if not explicitly set (matches backend default)
const getFollowLinksFromConfig = (config: Record<string, unknown>): boolean => {
  return (config?.follow_links ?? true) as boolean
}

const form = ref({
  name: props.channel?.name ?? '',
  connector_type: (props.channel?.connector_type ?? 'rss') as ConnectorType,
  url: props.channel ? getUrlFromConfig(props.channel.config) : '',
  enabled: props.channel?.enabled ?? true,
  fetch_interval_minutes: props.channel?.fetch_interval_minutes ?? 30,
  // Default to true to match backend default (RSS fetches full article content by default)
  follow_links: props.channel ? getFollowLinksFromConfig(props.channel.config) : true
})

// Check if connector type supports follow_links
const supportsFollowLinks = computed(() =>
  ['rss', 'google_alerts'].includes(form.value.connector_type)
)

const isEditing = computed(() => !!props.channel)

const currentConnector = computed(() =>
  connectors.value.find((c) => c.type === form.value.connector_type)
)

// Get the appropriate config field name for the connector type
const getConfigFieldName = (connectorType: ConnectorType): string => {
  switch (connectorType) {
    case 'x_scraper':
    case 'twitter':
    case 'bluesky':
    case 'mastodon':
    case 'instagram':
    case 'instagram_scraper':
      return 'handle'
    case 'telegram':
      return 'channel'
    default:
      return 'url'
  }
}

// Get placeholder text based on connector type
const getUrlPlaceholder = computed(() => {
  switch (form.value.connector_type) {
    case 'rss':
      return 'https://example.com/feed.xml'
    case 'html':
      return 'https://example.com/news'
    case 'x_scraper':
    case 'twitter':
      return '@username'
    case 'bluesky':
      return '@username.bsky.social'
    case 'mastodon':
      return '@user@instance.social'
    case 'telegram':
      return 'channelname (ohne @)'
    case 'instagram':
    case 'instagram_scraper':
      return 'username (ohne @)'
    case 'google_alerts':
      return 'https://www.google.com/alerts/feeds/...'
    case 'pdf':
      return 'https://example.com/document.pdf'
    default:
      return 'URL oder Handle'
  }
})

// Get label for URL field based on connector type
const getUrlLabel = computed(() => {
  switch (form.value.connector_type) {
    case 'x_scraper':
    case 'twitter':
    case 'bluesky':
    case 'mastodon':
    case 'instagram':
    case 'instagram_scraper':
      return 'Handle'
    case 'telegram':
      return 'Kanal-Name'
    default:
      return 'URL'
  }
})

// Build config object from form
const buildConfig = (): Record<string, unknown> => {
  const fieldName = getConfigFieldName(form.value.connector_type)
  const config: Record<string, unknown> = { [fieldName]: form.value.url }

  // Add follow_links for RSS/Google Alerts connectors
  if (supportsFollowLinks.value) {
    config.follow_links = form.value.follow_links
  }

  return config
}

const validateConfig = async () => {
  validating.value = true
  validationResult.value = null
  try {
    const response = await connectorsApi.validate(form.value.connector_type, buildConfig())
    validationResult.value = response.data
  } catch {
    validationResult.value = { valid: false, message: 'Validierung fehlgeschlagen' }
  } finally {
    validating.value = false
  }
}

const save = async () => {
  if (!props.source) return

  loading.value = true
  try {
    const config = buildConfig()

    if (isEditing.value && props.channel) {
      await sourcesStore.updateChannel(props.channel.id, {
        name: form.value.name || null,
        config,
        enabled: form.value.enabled,
        fetch_interval_minutes: form.value.fetch_interval_minutes
      })
    } else {
      await sourcesStore.addChannel(props.source.id, {
        name: form.value.name || null,
        connector_type: form.value.connector_type,
        config,
        enabled: form.value.enabled,
        fetch_interval_minutes: form.value.fetch_interval_minutes
      })
    }
    emit('saved')
  } finally {
    loading.value = false
  }
}

// Reset validation when connector type changes
watch(
  () => form.value.connector_type,
  () => {
    validationResult.value = null
  }
)

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
          {{ isEditing ? 'Kanal bearbeiten' : 'Neuer Kanal' }}
          <span v-if="source" class="text-sm font-normal text-gray-500">
            - {{ source.name }}
          </span>
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
          <!-- Connector Type (only for new channels) -->
          <div v-if="!isEditing">
            <label class="block text-sm font-medium text-gray-700">Connector-Typ *</label>
            <select v-model="form.connector_type" class="input mt-1">
              <option v-for="c in connectors" :key="c.type" :value="c.type">
                {{ c.name }}
              </option>
            </select>
            <p v-if="currentConnector" class="mt-1 text-xs text-gray-500">
              {{ currentConnector.description }}
            </p>
          </div>

          <!-- Connector type display for editing -->
          <div v-else>
            <label class="block text-sm font-medium text-gray-700">Connector-Typ</label>
            <p class="mt-1 text-sm text-gray-900">
              {{ currentConnector?.name || form.connector_type }}
            </p>
          </div>

          <!-- URL/Handle -->
          <div>
            <label class="block text-sm font-medium text-gray-700">{{ getUrlLabel }} *</label>
            <input
              v-model="form.url"
              type="text"
              class="input mt-1"
              required
              :placeholder="getUrlPlaceholder"
            />
          </div>

          <!-- Channel Name (optional) -->
          <div>
            <label class="block text-sm font-medium text-gray-700">Kanal-Name</label>
            <input
              v-model="form.name"
              type="text"
              class="input mt-1"
              placeholder="z.B. Aktuell, Politik, Rhein-Main (optional)"
            />
            <p class="mt-1 text-xs text-gray-500">
              Optional: Zur Unterscheidung mehrerer Kanäle gleichen Typs
            </p>
          </div>

          <!-- Fetch Interval -->
          <div>
            <label class="block text-sm font-medium text-gray-700">
              Abrufintervall (Minuten)
            </label>
            <input
              v-model.number="form.fetch_interval_minutes"
              type="number"
              min="5"
              max="1440"
              class="input mt-1"
            />
            <p class="mt-1 text-xs text-gray-500">
              Empfohlen: RSS 30 Min., Social Media 60 Min.
            </p>
          </div>

          <!-- Enabled -->
          <div class="flex items-center gap-2">
            <input
              id="channel_enabled"
              v-model="form.enabled"
              type="checkbox"
              class="rounded border-gray-300"
            />
            <label for="channel_enabled" class="text-sm text-gray-700">Kanal aktivieren</label>
          </div>

          <!-- Follow Links (RSS only) -->
          <div v-if="supportsFollowLinks" class="flex items-center gap-2">
            <input
              id="channel_follow_links"
              v-model="form.follow_links"
              type="checkbox"
              class="rounded border-gray-300"
            />
            <label for="channel_follow_links" class="text-sm text-gray-700">Links folgen</label>
            <span class="text-xs text-gray-500">(Verlinkte Artikel automatisch abrufen)</span>
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
              :class="
                validationResult.valid ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'
              "
            >
              {{ validationResult.message }}
            </div>
          </div>
        </div>

        <div class="mt-6 flex justify-end gap-3">
          <button type="button" class="btn btn-secondary" @click="emit('close')">Abbrechen</button>
          <button
            type="submit"
            class="btn btn-primary"
            :disabled="loading || !form.url.trim()"
          >
            {{ loading ? 'Speichere...' : isEditing ? 'Speichern' : 'Hinzufügen' }}
          </button>
        </div>
      </form>
    </div>
  </div>
</template>

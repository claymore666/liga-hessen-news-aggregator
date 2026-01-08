<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { useSourcesStore } from '@/stores'
import {
  PlusIcon,
  ArrowPathIcon,
  PencilIcon,
  TrashIcon,
  CheckCircleIcon,
  XCircleIcon,
  ExclamationCircleIcon,
  ChevronDownIcon,
  ChevronRightIcon
} from '@heroicons/vue/24/outline'
import { formatDistanceToNow } from 'date-fns'
import { de } from 'date-fns/locale'
import SourceFormModal from '@/components/SourceFormModal.vue'
import ChannelFormModal from '@/components/ChannelFormModal.vue'
import type { Source, Channel } from '@/types'

const sourcesStore = useSourcesStore()
const showCreateModal = ref(false)
const editingSource = ref<Source | null>(null)
const addingChannelToSource = ref<Source | null>(null)
const editingChannel = ref<{ channel: Channel; source: Source } | null>(null)
const expandedSources = ref<Set<number>>(new Set())
const filterType = ref<'all' | 'errors' | 'disabled'>('all')

const formatTime = (date: string | null) => {
  if (!date) return 'Nie'
  return formatDistanceToNow(new Date(date), { addSuffix: true, locale: de })
}

const connectorLabels: Record<string, string> = {
  rss: 'RSS',
  html: 'HTML',
  bluesky: 'Bluesky',
  twitter: 'Twitter',
  x_scraper: 'X.com',
  pdf: 'PDF',
  mastodon: 'Mastodon',
  telegram: 'Telegram',
  instagram: 'IG',
  instagram_scraper: 'IG',
  google_alerts: 'Alerts'
}

const connectorColors: Record<string, string> = {
  rss: 'bg-orange-100 text-orange-700',
  html: 'bg-blue-100 text-blue-700',
  bluesky: 'bg-sky-100 text-sky-700',
  twitter: 'bg-gray-100 text-gray-700',
  x_scraper: 'bg-gray-900 text-white',
  pdf: 'bg-red-100 text-red-700',
  mastodon: 'bg-purple-100 text-purple-700',
  telegram: 'bg-cyan-100 text-cyan-700',
  instagram: 'bg-pink-100 text-pink-700',
  instagram_scraper: 'bg-pink-100 text-pink-700',
  google_alerts: 'bg-green-100 text-green-700'
}

const filteredSources = computed(() => {
  let sources = sourcesStore.sources
  if (filterType.value === 'errors') {
    sources = sources.filter((s) => s.channels.some((c) => c.last_error))
  } else if (filterType.value === 'disabled') {
    sources = sources.filter((s) => !s.enabled || s.channels.some((c) => !c.enabled))
  }
  return sources
})

const toggleExpand = (sourceId: number) => {
  if (expandedSources.value.has(sourceId)) {
    expandedSources.value.delete(sourceId)
  } else {
    expandedSources.value.add(sourceId)
  }
}

const expandAll = () => {
  sourcesStore.sources.forEach((s) => expandedSources.value.add(s.id))
}

const collapseAll = () => {
  expandedSources.value.clear()
}

const deleteSource = async (source: Source) => {
  if (confirm(`Organisation "${source.name}" und alle ${source.channels.length} Kanäle wirklich löschen?`)) {
    await sourcesStore.deleteSource(source.id)
  }
}

const deleteChannel = async (channel: Channel, source: Source) => {
  const channelName = sourcesStore.getChannelDisplayName(channel)
  if (confirm(`Kanal "${channelName}" aus "${source.name}" wirklich löschen?`)) {
    await sourcesStore.deleteChannel(channel.id)
  }
}

const toggleSourceEnabled = async (source: Source) => {
  if (source.enabled) {
    await sourcesStore.disableSource(source.id)
  } else {
    await sourcesStore.enableSource(source.id)
  }
}

const toggleChannelEnabled = async (channel: Channel) => {
  if (channel.enabled) {
    await sourcesStore.disableChannel(channel.id)
  } else {
    await sourcesStore.enableChannel(channel.id)
  }
}

const getChannelIdentifier = (channel: Channel): string => {
  const config = channel.config as Record<string, string>
  return config.url || config.feed_url || config.handle || config.channel || channel.source_identifier || '-'
}

const onSourceSaved = () => {
  showCreateModal.value = false
  editingSource.value = null
}

const onChannelSaved = () => {
  addingChannelToSource.value = null
  editingChannel.value = null
}

onMounted(() => {
  sourcesStore.fetchSources()
})
</script>

<template>
  <div class="space-y-4">
    <!-- Header -->
    <div class="flex flex-wrap items-center justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold text-gray-900">Quellen</h1>
        <p class="text-sm text-gray-500">
          {{ sourcesStore.sources.length }} Organisationen,
          {{ sourcesStore.totalChannels }} Kanäle
          ({{ sourcesStore.enabledChannels }} aktiv)
        </p>
      </div>
      <div class="flex flex-wrap gap-2">
        <!-- Filter buttons -->
        <div class="flex rounded-md shadow-sm">
          <button
            type="button"
            class="rounded-l-md border border-gray-300 px-3 py-2 text-sm font-medium"
            :class="filterType === 'all' ? 'bg-liga-600 text-white' : 'bg-white text-gray-700 hover:bg-gray-50'"
            @click="filterType = 'all'"
          >
            Alle
          </button>
          <button
            type="button"
            class="-ml-px border border-gray-300 px-3 py-2 text-sm font-medium"
            :class="filterType === 'errors' ? 'bg-red-600 text-white' : 'bg-white text-gray-700 hover:bg-gray-50'"
            @click="filterType = 'errors'"
          >
            Fehler
          </button>
          <button
            type="button"
            class="-ml-px rounded-r-md border border-gray-300 px-3 py-2 text-sm font-medium"
            :class="filterType === 'disabled' ? 'bg-gray-600 text-white' : 'bg-white text-gray-700 hover:bg-gray-50'"
            @click="filterType = 'disabled'"
          >
            Deaktiviert
          </button>
        </div>
        <button type="button" class="btn btn-secondary text-sm" @click="expandAll">
          Alle öffnen
        </button>
        <button type="button" class="btn btn-secondary text-sm" @click="collapseAll">
          Alle schließen
        </button>
        <button
          type="button"
          class="btn btn-secondary"
          :disabled="sourcesStore.fetchingSourceId !== null"
          @click="sourcesStore.fetchAllChannels()"
        >
          <ArrowPathIcon
            class="mr-2 h-4 w-4"
            :class="{ 'animate-spin': sourcesStore.fetchingSourceId === -1 }"
          />
          Alle abrufen
        </button>
        <button type="button" class="btn btn-primary" @click="showCreateModal = true">
          <PlusIcon class="mr-2 h-4 w-4" />
          Neue Organisation
        </button>
      </div>
    </div>

    <!-- Loading state -->
    <div v-if="sourcesStore.loading" class="card py-12 text-center">
      <ArrowPathIcon class="mx-auto h-8 w-8 animate-spin text-gray-400" />
    </div>

    <!-- Empty state -->
    <div v-else-if="sourcesStore.sources.length === 0" class="card py-12 text-center">
      <p class="text-gray-500">Keine Quellen konfiguriert</p>
      <button type="button" class="btn btn-primary mt-4" @click="showCreateModal = true">
        Erste Organisation hinzufügen
      </button>
    </div>

    <!-- Sources list -->
    <div v-else class="space-y-3">
      <div
        v-for="source in filteredSources"
        :key="source.id"
        class="card overflow-hidden"
        :class="{ 'opacity-60': !source.enabled }"
      >
        <!-- Source header -->
        <div class="flex items-center justify-between p-4">
          <div class="flex min-w-0 flex-1 items-center gap-3">
            <button
              type="button"
              class="rounded p-1 hover:bg-gray-100"
              @click="toggleExpand(source.id)"
            >
              <component
                :is="expandedSources.has(source.id) ? ChevronDownIcon : ChevronRightIcon"
                class="h-5 w-5 text-gray-500"
              />
            </button>
            <button
              type="button"
              class="rounded p-1 hover:bg-gray-100"
              :title="source.enabled ? 'Deaktivieren' : 'Aktivieren'"
              @click="toggleSourceEnabled(source)"
            >
              <component
                :is="source.enabled ? CheckCircleIcon : XCircleIcon"
                class="h-5 w-5"
                :class="source.enabled ? 'text-green-500' : 'text-gray-400'"
              />
            </button>
            <div class="min-w-0 flex-1">
              <h3 class="truncate text-lg font-medium text-gray-900">
                {{ source.name }}
              </h3>
              <p class="text-sm text-gray-500">
                {{ source.channel_count }} Kanal{{ source.channel_count !== 1 ? 'e' : '' }}
                <span v-if="source.is_stakeholder" class="ml-2 text-liga-600">
                  Stakeholder
                </span>
              </p>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <button
              type="button"
              class="btn btn-secondary text-sm"
              :disabled="sourcesStore.fetchingSourceId === source.id"
              @click="sourcesStore.fetchAllSourceChannels(source.id)"
            >
              <ArrowPathIcon
                class="mr-1 h-4 w-4"
                :class="{ 'animate-spin': sourcesStore.fetchingSourceId === source.id }"
              />
              Alle abrufen
            </button>
            <button
              type="button"
              class="btn btn-secondary text-sm"
              @click="addingChannelToSource = source"
            >
              <PlusIcon class="mr-1 h-4 w-4" />
              Kanal
            </button>
            <button
              type="button"
              class="btn btn-secondary text-sm"
              @click="editingSource = source"
            >
              <PencilIcon class="h-4 w-4" />
            </button>
            <button type="button" class="btn btn-danger text-sm" @click="deleteSource(source)">
              <TrashIcon class="h-4 w-4" />
            </button>
          </div>
        </div>

        <!-- Channels table (collapsed by default, show summary) -->
        <div
          v-if="!expandedSources.has(source.id) && source.channels.length > 0"
          class="border-t border-gray-200 bg-gray-50 px-4 py-2"
        >
          <div class="flex flex-wrap gap-2">
            <span
              v-for="channel in source.channels"
              :key="channel.id"
              class="inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs"
              :class="[
                connectorColors[channel.connector_type] || 'bg-gray-100 text-gray-700',
                { 'opacity-50': !channel.enabled }
              ]"
            >
              <ExclamationCircleIcon
                v-if="channel.last_error"
                class="h-3 w-3 text-red-500"
              />
              {{ connectorLabels[channel.connector_type] || channel.connector_type }}
              <span v-if="channel.name" class="font-medium">{{ channel.name }}</span>
            </span>
          </div>
        </div>

        <!-- Expanded channels list -->
        <div
          v-if="expandedSources.has(source.id) && source.channels.length > 0"
          class="border-t border-gray-200"
        >
          <table class="min-w-full divide-y divide-gray-200">
            <thead class="bg-gray-50">
              <tr>
                <th class="px-4 py-2 text-left text-xs font-medium uppercase text-gray-500">
                  Status
                </th>
                <th class="px-4 py-2 text-left text-xs font-medium uppercase text-gray-500">
                  Typ
                </th>
                <th class="px-4 py-2 text-left text-xs font-medium uppercase text-gray-500">
                  Name/Identifier
                </th>
                <th class="px-4 py-2 text-left text-xs font-medium uppercase text-gray-500">
                  Intervall
                </th>
                <th class="px-4 py-2 text-left text-xs font-medium uppercase text-gray-500">
                  Letzter Abruf
                </th>
                <th class="px-4 py-2 text-right text-xs font-medium uppercase text-gray-500">
                  Aktionen
                </th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-200 bg-white">
              <tr
                v-for="channel in source.channels"
                :key="channel.id"
                :class="{ 'bg-gray-50 opacity-60': !channel.enabled }"
              >
                <td class="whitespace-nowrap px-4 py-2">
                  <button
                    type="button"
                    class="rounded p-1 hover:bg-gray-100"
                    :disabled="!source.enabled"
                    :title="channel.enabled ? 'Deaktivieren' : 'Aktivieren'"
                    @click="toggleChannelEnabled(channel)"
                  >
                    <component
                      :is="channel.enabled ? CheckCircleIcon : XCircleIcon"
                      class="h-5 w-5"
                      :class="channel.enabled ? 'text-green-500' : 'text-gray-400'"
                    />
                  </button>
                </td>
                <td class="whitespace-nowrap px-4 py-2">
                  <span
                    class="inline-flex rounded px-2 py-0.5 text-xs font-medium"
                    :class="connectorColors[channel.connector_type] || 'bg-gray-100 text-gray-700'"
                  >
                    {{ connectorLabels[channel.connector_type] || channel.connector_type }}
                  </span>
                </td>
                <td class="max-w-xs truncate px-4 py-2 text-sm">
                  <span v-if="channel.name" class="font-medium text-gray-900">
                    {{ channel.name }}
                  </span>
                  <span class="text-gray-500" :class="{ 'ml-1': channel.name }">
                    {{ getChannelIdentifier(channel) }}
                  </span>
                </td>
                <td class="whitespace-nowrap px-4 py-2 text-sm text-gray-500">
                  {{ channel.fetch_interval_minutes }} Min.
                </td>
                <td class="whitespace-nowrap px-4 py-2 text-sm">
                  <span v-if="channel.last_error" class="flex items-center gap-1 text-red-600">
                    <ExclamationCircleIcon class="h-4 w-4" />
                    Fehler
                  </span>
                  <span v-else class="text-gray-500">
                    {{ formatTime(channel.last_fetch_at) }}
                  </span>
                </td>
                <td class="whitespace-nowrap px-4 py-2 text-right">
                  <div class="flex justify-end gap-1">
                    <button
                      type="button"
                      class="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
                      :disabled="sourcesStore.fetchingChannelId === channel.id || !source.enabled"
                      @click="sourcesStore.fetchChannel(channel.id)"
                    >
                      <ArrowPathIcon
                        class="h-4 w-4"
                        :class="{ 'animate-spin': sourcesStore.fetchingChannelId === channel.id }"
                      />
                    </button>
                    <button
                      type="button"
                      class="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
                      @click="editingChannel = { channel, source }"
                    >
                      <PencilIcon class="h-4 w-4" />
                    </button>
                    <button
                      type="button"
                      class="rounded p-1 text-gray-400 hover:bg-red-100 hover:text-red-600"
                      @click="deleteChannel(channel, source)"
                    >
                      <TrashIcon class="h-4 w-4" />
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- No channels -->
        <div
          v-if="source.channels.length === 0"
          class="border-t border-gray-200 bg-gray-50 px-4 py-3 text-center text-sm text-gray-500"
        >
          Keine Kanäle konfiguriert.
          <button
            type="button"
            class="ml-1 text-liga-600 hover:underline"
            @click="addingChannelToSource = source"
          >
            Kanal hinzufügen
          </button>
        </div>
      </div>
    </div>

    <!-- Create/Edit Source Modal -->
    <SourceFormModal
      v-if="showCreateModal || editingSource"
      :source="editingSource"
      @close="
        showCreateModal = false;
        editingSource = null
      "
      @saved="onSourceSaved"
    />

    <!-- Add/Edit Channel Modal -->
    <ChannelFormModal
      v-if="addingChannelToSource || editingChannel"
      :source="addingChannelToSource || editingChannel?.source"
      :channel="editingChannel?.channel"
      @close="
        addingChannelToSource = null;
        editingChannel = null
      "
      @saved="onChannelSaved"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import axios from 'axios'

const props = defineProps<{
  days: number
}>()

const router = useRouter()

interface TopicCount {
  topic: string
  count: number
}

const topics = ref<TopicCount[]>([])
const loading = ref(false)
const totalItems = ref(0)

const pastelColors = [
  '#6366f1', '#8b5cf6', '#a855f7', '#d946ef', '#ec4899',
  '#f43f5e', '#f97316', '#eab308', '#84cc16', '#22c55e',
  '#14b8a6', '#06b6d4', '#0ea5e9', '#3b82f6', '#2563eb',
]

function getFontSize(count: number, min: number, max: number): string {
  if (max === min) return '1.5rem'
  const ratio = (count - min) / (max - min)
  const size = 0.75 + ratio * 2.25
  return `${size.toFixed(2)}rem`
}

function getColor(index: number): string {
  return pastelColors[index % pastelColors.length]
}

async function fetchTopics() {
  loading.value = true
  try {
    const params: Record<string, any> = { days: props.days }
    if (props.days >= 30) params.limit = 10
    const { data } = await axios.get('/api/stats/topic-counts', { params })
    topics.value = data.topics
    totalItems.value = data.total_items
  } catch (e) {
    console.error('Failed to fetch topics', e)
  } finally {
    loading.value = false
  }
}

function navigateToTopic(topic: string) {
  router.push({ path: '/items', query: { topic } })
}

const minCount = computed(() => Math.min(...topics.value.map(t => t.count), 0))
const maxCount = computed(() => Math.max(...topics.value.map(t => t.count), 1))

watch(() => props.days, fetchTopics, { immediate: true })
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-4">
      <h2 class="text-lg font-semibold text-gray-700">Themen</h2>
    </div>

    <div v-if="loading" class="flex items-center justify-center h-48">
      <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
    </div>

    <div v-else-if="topics.length === 0" class="flex items-center justify-center h-48 text-gray-400">
      Keine Themen im gewählten Zeitraum
    </div>

    <div v-else class="flex flex-wrap items-center justify-center gap-3 min-h-[12rem]">
      <span
        v-for="(t, i) in topics"
        :key="t.topic"
        class="cursor-pointer hover:opacity-70 transition-opacity font-semibold leading-tight"
        :style="{
          fontSize: getFontSize(t.count, minCount, maxCount),
          color: getColor(i),
        }"
        :title="`${t.topic}: ${t.count} Artikel`"
        @click="navigateToTopic(t.topic)"
      >
        {{ t.topic }}
      </span>
    </div>

    <p class="text-xs text-gray-400 mt-3 text-center">
      {{ totalItems }} Artikel in {{ days }} Tag{{ days !== 1 ? 'en' : '' }}
    </p>
  </div>
</template>

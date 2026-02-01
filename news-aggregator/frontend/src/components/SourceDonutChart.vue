<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { Doughnut } from 'vue-chartjs'
import {
  Chart as ChartJS,
  ArcElement,
  Tooltip,
  Legend,
} from 'chart.js'
import axios from 'axios'

ChartJS.register(ArcElement, Tooltip, Legend)

interface DataItem {
  name: string
  count: number
}

const items = ref<DataItem[]>([])
const loading = ref(false)
const selectedPriority = ref<string | null>(null)
const selectedRange = ref<string>('all')
const resolveGa = ref<string | null>(null)

const priorities = [
  { key: null, label: 'Alle' },
  { key: 'high', label: 'H' },
  { key: 'medium', label: 'M' },
  { key: 'low', label: 'L' },
]

const ranges = [
  { key: '1d', label: '1T', days: 1 },
  { key: '3d', label: '3T', days: 3 },
  { key: '1w', label: '1W', days: 7 },
  { key: '1m', label: '1M', days: 30 },
  { key: 'all', label: 'Alle', days: null },
]

const gaOptions = [
  { key: null, label: 'GA', tooltip: 'Google Alerts als eine Quelle anzeigen' },
  { key: 'keyword', label: 'GA Keyword', tooltip: 'Google Alerts nach Suchbegriff aufschlüsseln' },
  { key: 'source', label: 'GA Quelle', tooltip: 'Google Alerts nach Ursprungsmedium aufschlüsseln' },
]

const chartColors = [
  '#3b82f6', '#ef4444', '#22c55e', '#f59e0b', '#8b5cf6',
  '#ec4899', '#14b8a6', '#f97316', '#06b6d4', '#84cc16',
  '#9ca3af',
]

async function fetchData() {
  loading.value = true
  try {
    const rangeDays = ranges.find(r => r.key === selectedRange.value)?.days
    const params: Record<string, any> = {}
    if (rangeDays !== null && rangeDays !== undefined) params.days = rangeDays
    if (selectedPriority.value) params.priority = selectedPriority.value
    if (resolveGa.value) params.resolve_ga = resolveGa.value

    const { data } = await axios.get('/api/stats/source-donut', { params })
    items.value = data
  } catch (e) {
    console.error('Failed to fetch data', e)
  } finally {
    loading.value = false
  }
}

const top10 = computed(() => {
  const sorted = [...items.value]
    .filter(s => s.count > 0)
    .sort((a, b) => b.count - a.count)

  if (sorted.length <= 10) return { items: sorted, otherCount: 0 }

  const top = sorted.slice(0, 10)
  const otherCount = sorted.slice(10).reduce((sum, s) => sum + s.count, 0)
  return { items: top, otherCount }
})

const chartData = computed(() => {
  const { items: topItems, otherCount } = top10.value
  const labels = topItems.map(s => s.name)
  const data = topItems.map(s => s.count)
  const colors = topItems.map((_, i) => chartColors[i % chartColors.length])

  if (otherCount > 0) {
    labels.push('Andere')
    data.push(otherCount)
    colors.push(chartColors[10])
  }

  return {
    labels,
    datasets: [{
      data,
      backgroundColor: colors,
      borderWidth: 1,
      borderColor: '#fff',
    }]
  }
})

const chartOptions = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: {
    legend: {
      position: 'right' as const,
      labels: {
        boxWidth: 12,
        padding: 8,
        font: { size: 11 },
      }
    },
    tooltip: {
      callbacks: {
        label: (ctx: any) => ` ${ctx.label}: ${ctx.raw} Artikel`
      }
    }
  }
}

function selectPriority(key: string | null) {
  selectedPriority.value = key
  fetchData()
}

function selectRange(key: string) {
  selectedRange.value = key
  fetchData()
}

function selectGa(key: string | null) {
  resolveGa.value = key
  fetchData()
}

onMounted(fetchData)
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-2">
      <h2 class="text-lg font-semibold text-gray-700">Quellen</h2>
      <div class="flex gap-1">
        <button
          v-for="p in priorities"
          :key="String(p.key)"
          class="px-2 py-1 text-xs rounded font-medium transition-colors"
          :class="selectedPriority === p.key
            ? 'bg-blue-600 text-white'
            : 'bg-gray-100 text-gray-600 hover:bg-gray-200'"
          @click="selectPriority(p.key)"
        >
          {{ p.label }}
        </button>
      </div>
    </div>

    <div class="flex items-center justify-between mb-4">
      <div class="flex gap-1">
        <button
          v-for="g in gaOptions"
          :key="String(g.key)"
          class="px-2 py-1 text-xs rounded font-medium transition-colors"
          :class="resolveGa === g.key
            ? 'bg-indigo-600 text-white'
            : 'bg-gray-100 text-gray-600 hover:bg-gray-200'"
          :title="g.tooltip"
          @click="selectGa(g.key)"
        >
          {{ g.label }}
        </button>
      </div>
      <div class="flex gap-1">
        <button
          v-for="r in ranges"
          :key="r.key"
          class="px-2 py-1 text-xs rounded font-medium transition-colors"
          :class="selectedRange === r.key
            ? 'bg-blue-600 text-white'
            : 'bg-gray-100 text-gray-600 hover:bg-gray-200'"
          @click="selectRange(r.key)"
        >
          {{ r.label }}
        </button>
      </div>
    </div>

    <div v-if="loading" class="flex items-center justify-center h-64">
      <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
    </div>

    <div v-else-if="top10.items.length === 0" class="flex items-center justify-center h-64 text-gray-400">
      Keine Daten verfügbar
    </div>

    <div v-else class="h-64">
      <Doughnut :data="chartData" :options="chartOptions" />
    </div>
  </div>
</template>

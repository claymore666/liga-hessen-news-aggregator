<script setup lang="ts">
import { computed } from 'vue'
import type { Priority } from '@/types'

const props = defineProps<{
  priority: Priority
  size?: 'sm' | 'md'
}>()

const priorityConfig = {
  high: { label: 'Hoch', class: 'badge-high' },
  medium: { label: 'Mittel', class: 'badge-medium' },
  low: { label: 'Niedrig', class: 'badge-low' },
  none: { label: 'Keine', class: 'badge-none' }
}

const sizeClass = computed(() => props.size === 'sm' ? 'text-[10px] px-1.5 py-0' : '')
</script>

<template>
  <span class="badge" :class="[priorityConfig[priority].class, sizeClass]">
    {{ priorityConfig[priority].label }}
  </span>
</template>

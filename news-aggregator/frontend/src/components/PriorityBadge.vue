<script setup lang="ts">
import type { Priority } from '@/types'

defineProps<{
  priority: Priority
}>()

const priorityConfig = {
  critical: { label: 'Kritisch', class: 'badge-critical' },
  high: { label: 'Hoch', class: 'badge-high' },
  medium: { label: 'Mittel', class: 'badge-medium' },
  low: { label: 'Niedrig', class: 'badge-low' }
}
</script>

<template>
  <span class="badge" :class="priorityConfig[priority].class">
    {{ priorityConfig[priority].label }}
  </span>
</template>

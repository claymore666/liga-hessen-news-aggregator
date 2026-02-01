<script setup lang="ts">
import TopicWordCloud from '@/components/TopicWordCloud.vue'
import SourceDonutChart from '@/components/SourceDonutChart.vue'
</script>

<template>
  <div>
    <h1 class="text-2xl font-bold text-gray-800 mb-6">Übersicht</h1>
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <div class="bg-white rounded-xl shadow p-6">
        <TopicWordCloud />
      </div>
      <div class="bg-white rounded-xl shadow p-6">
        <SourceDonutChart />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSourcesStore } from '@/stores'
import { ArrowLeftIcon } from '@heroicons/vue/24/outline'

const route = useRoute()
const router = useRouter()
const sourcesStore = useSourcesStore()

onMounted(() => {
  sourcesStore.fetchSource(parseInt(route.params.id as string))
})
</script>

<template>
  <div class="space-y-4">
    <button
      type="button"
      class="flex items-center text-sm text-gray-500 hover:text-gray-700"
      @click="router.back()"
    >
      <ArrowLeftIcon class="mr-2 h-4 w-4" />
      Zurück zu Quellen
    </button>

    <div v-if="sourcesStore.currentSource" class="card">
      <h1 class="text-2xl font-bold text-gray-900">
        {{ sourcesStore.currentSource.name }}
      </h1>
      <p class="mt-2 text-gray-500">
        {{ sourcesStore.currentSource.url }}
      </p>
    </div>
  </div>
</template>

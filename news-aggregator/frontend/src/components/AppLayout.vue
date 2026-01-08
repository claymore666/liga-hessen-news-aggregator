<script setup lang="ts">
import { ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import {
  HomeIcon,
  NewspaperIcon,
  RssIcon,
  AdjustmentsHorizontalIcon,
  Cog6ToothIcon,
  Bars3Icon,
  XMarkIcon
} from '@heroicons/vue/24/outline'

const route = useRoute()
const sidebarOpen = ref(false)

const navigation = [
  { name: 'Dashboard', to: '/', icon: HomeIcon },
  { name: 'Nachrichten', to: '/items', icon: NewspaperIcon },
  { name: 'Quellen', to: '/sources', icon: RssIcon },
  { name: 'Regeln', to: '/rules', icon: AdjustmentsHorizontalIcon },
  { name: 'Einstellungen', to: '/settings', icon: Cog6ToothIcon }
]

const isActive = (path: string) => {
  if (path === '/') return route.path === '/'
  return route.path.startsWith(path)
}
</script>

<template>
  <div class="min-h-screen bg-gray-200">
    <!-- Mobile sidebar backdrop -->
    <div
      v-if="sidebarOpen"
      class="fixed inset-0 z-40 bg-blue-900 bg-opacity-50 lg:hidden"
      @click="sidebarOpen = false"
    />

    <!-- Mobile sidebar -->
    <div
      v-if="sidebarOpen"
      class="fixed inset-y-0 left-0 z-50 w-64 bg-blue-100 shadow-xl lg:hidden"
    >
      <div class="flex h-16 items-center justify-between px-4 border-b border-blue-300">
        <span class="text-xl font-bold text-blue-800">Liga News</span>
        <button
          type="button"
          class="text-blue-600 hover:text-blue-800"
          @click="sidebarOpen = false"
        >
          <XMarkIcon class="h-6 w-6" />
        </button>
      </div>
      <nav class="mt-4 px-2">
        <RouterLink
          v-for="item in navigation"
          :key="item.name"
          :to="item.to"
          class="flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors"
          :class="[
            isActive(item.to)
              ? 'bg-blue-300 text-blue-900'
              : 'text-blue-800 hover:bg-blue-200'
          ]"
          @click="sidebarOpen = false"
        >
          <component :is="item.icon" class="h-5 w-5" />
          {{ item.name }}
        </RouterLink>
      </nav>
    </div>

    <!-- Desktop sidebar -->
    <div class="hidden lg:fixed lg:inset-y-0 lg:flex lg:w-64 lg:flex-col">
      <div class="flex min-h-0 flex-1 flex-col border-r border-blue-300 bg-blue-100">
        <div class="flex h-16 items-center px-6 border-b border-blue-300">
          <span class="text-xl font-bold text-blue-800">Liga News</span>
        </div>
        <nav class="mt-4 flex-1 px-3">
          <RouterLink
            v-for="item in navigation"
            :key="item.name"
            :to="item.to"
            class="mb-1 flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors"
            :class="[
              isActive(item.to)
                ? 'bg-blue-300 text-blue-900'
                : 'text-blue-800 hover:bg-blue-200'
            ]"
          >
            <component :is="item.icon" class="h-5 w-5" />
            {{ item.name }}
          </RouterLink>
        </nav>
        <div class="border-t border-blue-300 p-4">
          <p class="text-xs text-blue-600">
            Liga der Freien Wohlfahrtspflege Hessen
          </p>
        </div>
      </div>
    </div>

    <!-- Main content -->
    <div class="lg:pl-64">
      <!-- Mobile header -->
      <div class="sticky top-0 z-10 flex h-16 items-center gap-4 border-b border-blue-300 bg-blue-100 px-4 lg:hidden">
        <button
          type="button"
          class="text-blue-600 hover:text-blue-800"
          @click="sidebarOpen = true"
        >
          <Bars3Icon class="h-6 w-6" />
        </button>
        <span class="text-lg font-semibold text-blue-800">Liga News</span>
      </div>

      <main class="p-4 lg:p-8">
        <slot />
      </main>
    </div>
  </div>
</template>

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
  <div class="min-h-screen bg-gray-50">
    <!-- Mobile sidebar backdrop -->
    <div
      v-if="sidebarOpen"
      class="fixed inset-0 z-40 bg-gray-600 bg-opacity-75 lg:hidden"
      @click="sidebarOpen = false"
    />

    <!-- Mobile sidebar -->
    <div
      v-if="sidebarOpen"
      class="fixed inset-y-0 left-0 z-50 w-64 bg-white shadow-xl lg:hidden"
    >
      <div class="flex h-16 items-center justify-between px-4">
        <span class="text-xl font-bold text-liga-600">Liga News</span>
        <button
          type="button"
          class="text-gray-500 hover:text-gray-700"
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
              ? 'bg-liga-50 text-liga-600'
              : 'text-gray-700 hover:bg-gray-100'
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
      <div class="flex min-h-0 flex-1 flex-col border-r border-gray-200 bg-white">
        <div class="flex h-16 items-center px-6">
          <span class="text-xl font-bold text-liga-600">Liga News</span>
        </div>
        <nav class="mt-4 flex-1 px-3">
          <RouterLink
            v-for="item in navigation"
            :key="item.name"
            :to="item.to"
            class="mb-1 flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors"
            :class="[
              isActive(item.to)
                ? 'bg-liga-50 text-liga-600'
                : 'text-gray-700 hover:bg-gray-100'
            ]"
          >
            <component :is="item.icon" class="h-5 w-5" />
            {{ item.name }}
          </RouterLink>
        </nav>
        <div class="border-t border-gray-200 p-4">
          <p class="text-xs text-gray-500">
            Liga der Freien Wohlfahrtspflege Hessen
          </p>
        </div>
      </div>
    </div>

    <!-- Main content -->
    <div class="lg:pl-64">
      <!-- Mobile header -->
      <div class="sticky top-0 z-10 flex h-16 items-center gap-4 border-b border-gray-200 bg-white px-4 lg:hidden">
        <button
          type="button"
          class="text-gray-500 hover:text-gray-700"
          @click="sidebarOpen = true"
        >
          <Bars3Icon class="h-6 w-6" />
        </button>
        <span class="text-lg font-semibold text-liga-600">Liga News</span>
      </div>

      <main class="p-4 lg:p-8">
        <slot />
      </main>
    </div>
  </div>
</template>

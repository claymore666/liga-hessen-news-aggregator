<script setup lang="ts">
import { ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import {
  HomeIcon,
  NewspaperIcon,
  RssIcon,
  AdjustmentsHorizontalIcon,
  Cog6ToothIcon,
  ChartBarIcon,
  Bars3Icon,
  XMarkIcon,
  ChevronLeftIcon,
  ChevronRightIcon
} from '@heroicons/vue/24/outline'
import { useUiStore } from '@/stores'

const route = useRoute()
const uiStore = useUiStore()
const sidebarOpen = ref(false)

const navigation = [
  { name: 'Dashboard', to: '/', icon: HomeIcon },
  { name: 'Nachrichten', to: '/items', icon: NewspaperIcon },
  { name: 'Quellen', to: '/sources', icon: RssIcon },
  { name: 'Regeln', to: '/rules', icon: AdjustmentsHorizontalIcon },
  { name: 'System', to: '/stats', icon: ChartBarIcon },
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
    <div
      class="hidden lg:fixed lg:inset-y-0 lg:left-0 lg:flex lg:flex-col transition-all duration-300"
      :class="uiStore.sidebarCollapsed ? 'lg:w-16' : 'lg:w-64'"
    >
      <div class="flex min-h-0 flex-1 flex-col border-r border-blue-300 bg-blue-100">
        <!-- Header -->
        <div class="flex h-16 items-center border-b border-blue-300" :class="uiStore.sidebarCollapsed ? 'justify-center px-2' : 'px-6'">
          <span v-if="!uiStore.sidebarCollapsed" class="text-xl font-bold text-blue-800">Liga News</span>
          <span v-else class="text-xl font-bold text-blue-800">LN</span>
        </div>

        <!-- Navigation -->
        <nav class="mt-4 flex-1" :class="uiStore.sidebarCollapsed ? 'px-2' : 'px-3'">
          <RouterLink
            v-for="item in navigation"
            :key="item.name"
            :to="item.to"
            class="mb-1 flex items-center rounded-lg py-2 text-sm font-medium transition-colors"
            :class="[
              isActive(item.to)
                ? 'bg-blue-300 text-blue-900'
                : 'text-blue-800 hover:bg-blue-200',
              uiStore.sidebarCollapsed ? 'justify-center px-2' : 'gap-3 px-3'
            ]"
            :title="uiStore.sidebarCollapsed ? item.name : undefined"
          >
            <component :is="item.icon" class="h-5 w-5 flex-shrink-0" />
            <span v-if="!uiStore.sidebarCollapsed">{{ item.name }}</span>
          </RouterLink>
        </nav>

        <!-- Footer with collapse toggle -->
        <div class="border-t border-blue-300 p-2">
          <button
            type="button"
            class="flex w-full items-center justify-center rounded-lg py-2 text-blue-600 hover:bg-blue-200 hover:text-blue-800 transition-colors"
            :title="uiStore.sidebarCollapsed ? 'Sidebar erweitern' : 'Sidebar einklappen'"
            @click="uiStore.toggleSidebar()"
          >
            <ChevronLeftIcon v-if="!uiStore.sidebarCollapsed" class="h-5 w-5" />
            <ChevronRightIcon v-else class="h-5 w-5" />
          </button>
          <p v-if="!uiStore.sidebarCollapsed" class="mt-2 text-xs text-blue-600 text-center">
            Liga der Freien Wohlfahrtspflege Hessen
          </p>
        </div>
      </div>
    </div>

    <!-- Main content -->
    <div
      class="transition-all duration-300"
      :class="uiStore.sidebarCollapsed ? 'lg:pl-16' : 'lg:pl-64'"
    >
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

      <main class="p-4 lg:p-6">
        <slot />
      </main>
    </div>
  </div>
</template>

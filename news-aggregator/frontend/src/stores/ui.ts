import { ref, computed } from 'vue'
import { defineStore } from 'pinia'

export const useUiStore = defineStore('ui', () => {
  // Load initial state from localStorage
  const storedCollapsed = localStorage.getItem('sidebarCollapsed')
  const sidebarCollapsed = ref(storedCollapsed === 'true')

  const toggleSidebar = () => {
    sidebarCollapsed.value = !sidebarCollapsed.value
    localStorage.setItem('sidebarCollapsed', String(sidebarCollapsed.value))
  }

  const setSidebarCollapsed = (collapsed: boolean) => {
    sidebarCollapsed.value = collapsed
    localStorage.setItem('sidebarCollapsed', String(collapsed))
  }

  // Sidebar width in pixels
  const sidebarWidth = computed(() => sidebarCollapsed.value ? 64 : 256)

  // Extra space available when collapsed (256 - 64 = 192px)
  // 50% of this goes to message list
  const messageListExtraWidth = computed(() => sidebarCollapsed.value ? 96 : 0)

  return {
    sidebarCollapsed,
    sidebarWidth,
    messageListExtraWidth,
    toggleSidebar,
    setSidebarCollapsed
  }
})

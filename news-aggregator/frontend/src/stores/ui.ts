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

  // Grid template columns for the two-column layout
  // Always 50-50 split - sidebar width affects both equally
  const messageListGridColumns = computed(() => '1fr 1fr')

  return {
    sidebarCollapsed,
    sidebarWidth,
    messageListGridColumns,
    toggleSidebar,
    setSidebarCollapsed
  }
})

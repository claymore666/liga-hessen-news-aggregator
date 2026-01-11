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
  // When collapsed: 1/3 inbox, 2/3 detail (using fr units)
  // When expanded: fixed 400px inbox, rest for detail
  const messageListGridColumns = computed(() =>
    sidebarCollapsed.value ? '1fr 2fr' : 'minmax(0, 400px) 1fr'
  )

  return {
    sidebarCollapsed,
    sidebarWidth,
    messageListGridColumns,
    toggleSidebar,
    setSidebarCollapsed
  }
})

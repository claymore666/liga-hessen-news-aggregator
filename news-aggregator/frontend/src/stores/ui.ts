import { ref, computed } from 'vue'
import { defineStore } from 'pinia'

function defaultPeriodDays(): number {
  return new Date().getDay() === 1 ? 3 : 1
}

export const useUiStore = defineStore('ui', () => {
  // Load initial state from localStorage
  const storedCollapsed = localStorage.getItem('sidebarCollapsed')
  const sidebarCollapsed = ref(storedCollapsed === 'true')

  // Übersicht time period (days)
  const periodDays = ref<number>(defaultPeriodDays())

  const setPeriodDays = (days: number) => {
    periodDays.value = days
  }

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

  return {
    sidebarCollapsed,
    sidebarWidth,
    toggleSidebar,
    setSidebarCollapsed,
    periodDays,
    setPeriodDays,
  }
})

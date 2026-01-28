<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { motdApi, type MOTDResponse } from '@/api'
import { XMarkIcon, InformationCircleIcon } from '@heroicons/vue/24/outline'

const motd = ref<MOTDResponse | null>(null)
const isVisible = ref(false)
const loading = ref(false)

// Session key in localStorage
const SESSION_KEY = 'motd_session'

interface MOTDSession {
  dismissedAt: string
  motdId: number | null
}

/**
 * Check if session has expired (expired at midnight Europe/Berlin)
 */
function isSessionExpired(): boolean {
  const sessionData = localStorage.getItem(SESSION_KEY)
  if (!sessionData) return true

  try {
    const session: MOTDSession = JSON.parse(sessionData)
    const dismissedDate = new Date(session.dismissedAt)
    const now = new Date()

    // Compare dates (ignore time) - session expires at midnight
    const dismissedDay = dismissedDate.toLocaleDateString('de-DE', { timeZone: 'Europe/Berlin' })
    const today = now.toLocaleDateString('de-DE', { timeZone: 'Europe/Berlin' })

    return dismissedDay !== today
  } catch {
    return true
  }
}

/**
 * Check if this specific MOTD was already dismissed today
 */
function wasMotdDismissed(motdId: number | null): boolean {
  if (isSessionExpired()) return false

  const sessionData = localStorage.getItem(SESSION_KEY)
  if (!sessionData) return false

  try {
    const session: MOTDSession = JSON.parse(sessionData)
    return session.motdId === motdId
  } catch {
    return false
  }
}

/**
 * Save dismissal to localStorage
 */
function saveDismissal(motdId: number | null) {
  const session: MOTDSession = {
    dismissedAt: new Date().toISOString(),
    motdId,
  }
  localStorage.setItem(SESSION_KEY, JSON.stringify(session))
}

/**
 * Dismiss the MOTD modal
 */
function dismiss() {
  if (motd.value) {
    saveDismissal(motd.value.id)
  }
  isVisible.value = false
}

/**
 * Fetch MOTD from API and show if needed
 */
async function checkMOTD() {
  loading.value = true
  try {
    const response = await motdApi.get()
    motd.value = response.data

    // Show if there's an active message and user hasn't dismissed it today
    if (motd.value.active && motd.value.message && !wasMotdDismissed(motd.value.id)) {
      isVisible.value = true
    }
  } catch (e) {
    console.error('Failed to fetch MOTD:', e)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  checkMOTD()
})

// Format date for display
const formattedDate = computed(() => {
  if (!motd.value?.updated_at) return ''
  const date = new Date(motd.value.updated_at)
  return date.toLocaleDateString('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  })
})
</script>

<template>
  <!-- Modal Backdrop -->
  <Teleport to="body">
    <Transition
      enter-active-class="transition-opacity duration-200"
      enter-from-class="opacity-0"
      enter-to-class="opacity-100"
      leave-active-class="transition-opacity duration-200"
      leave-from-class="opacity-100"
      leave-to-class="opacity-0"
    >
      <div
        v-if="isVisible"
        class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
        @click.self="dismiss"
      >
        <!-- Modal Content -->
        <Transition
          enter-active-class="transition-all duration-200"
          enter-from-class="opacity-0 scale-95"
          enter-to-class="opacity-100 scale-100"
          leave-active-class="transition-all duration-200"
          leave-from-class="opacity-100 scale-100"
          leave-to-class="opacity-0 scale-95"
        >
          <div
            v-if="isVisible"
            class="bg-white rounded-xl shadow-2xl max-w-lg w-full overflow-hidden"
          >
            <!-- Header -->
            <div class="bg-gradient-to-r from-blue-600 to-blue-700 px-6 py-4 flex items-center justify-between">
              <div class="flex items-center gap-3">
                <InformationCircleIcon class="h-6 w-6 text-white" />
                <h2 class="text-lg font-semibold text-white">Neuigkeiten</h2>
              </div>
              <button
                type="button"
                class="text-white/80 hover:text-white transition-colors p-1 rounded-lg hover:bg-white/10"
                @click="dismiss"
              >
                <XMarkIcon class="h-5 w-5" />
              </button>
            </div>

            <!-- Body -->
            <div class="px-6 py-5">
              <div
                class="prose prose-sm max-w-none text-gray-700"
                v-html="motd?.message?.replace(/\n/g, '<br>')"
              />
            </div>

            <!-- Footer -->
            <div class="px-6 py-4 bg-gray-50 border-t border-gray-100 flex items-center justify-between">
              <span class="text-xs text-gray-400">{{ formattedDate }}</span>
              <button
                type="button"
                class="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 transition-colors"
                @click="dismiss"
              >
                Verstanden
              </button>
            </div>
          </div>
        </Transition>
      </div>
    </Transition>
  </Teleport>
</template>

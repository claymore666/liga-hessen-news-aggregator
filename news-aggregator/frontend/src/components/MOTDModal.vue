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
  motdUpdatedAt: string | null  // Track the version of MOTD seen
}

/**
 * Check if this MOTD was already seen by the user.
 * Returns true if user has already seen this exact MOTD version.
 *
 * Logic:
 * - If no session exists, user hasn't seen any MOTD
 * - If motdId matches AND motdUpdatedAt matches, user has seen this version
 * - If motdId or updatedAt differs, this is a new/updated MOTD
 */
function hasSeenMotd(motdId: number | null, motdUpdatedAt: string | null): boolean {
  const sessionData = localStorage.getItem(SESSION_KEY)
  if (!sessionData) return false

  try {
    const session: MOTDSession = JSON.parse(sessionData)
    // User has seen this MOTD if both ID and updated_at match
    return session.motdId === motdId && session.motdUpdatedAt === motdUpdatedAt
  } catch {
    return false
  }
}

/**
 * Save that user has seen this MOTD version
 */
function saveDismissal(motdId: number | null, motdUpdatedAt: string | null) {
  const session: MOTDSession = {
    dismissedAt: new Date().toISOString(),
    motdId,
    motdUpdatedAt,
  }
  localStorage.setItem(SESSION_KEY, JSON.stringify(session))
}

/**
 * Dismiss the MOTD modal
 */
function dismiss() {
  if (motd.value) {
    saveDismissal(motd.value.id, motd.value.updated_at)
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

    // Show if there's an active message and user hasn't seen this version yet
    // Only show if the MOTD has actually changed since user last saw it
    if (motd.value.active && motd.value.message && !hasSeenMotd(motd.value.id, motd.value.updated_at)) {
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

/**
 * Format MOTD message for display.
 * Splits numbered items like "(1) ... (2) ... (3) ..." into a list,
 * with the text before the first number as a heading.
 */
const formattedMessage = computed(() => {
  const raw = motd.value?.message
  if (!raw) return ''

  // Split on (1), (2), (3), etc.
  const parts = raw.split(/\(\d+\)\s*/)
  if (parts.length <= 1) {
    // No numbered items — just convert newlines
    return raw.replace(/\n/g, '<br>')
  }

  const intro = parts[0].trim()
  const items = parts.slice(1).filter(s => s.trim())

  let html = ''
  if (intro) {
    html += `<p class="mb-3 font-medium">${intro.replace(/\n/g, '<br>')}</p>`
  }
  html += '<ul class="space-y-2 list-none pl-0">'
  items.forEach(item => {
    html += `<li class="flex gap-2 items-start"><span class="mt-0.5 shrink-0 w-1.5 h-1.5 rounded-full bg-blue-500"></span><span>${item.trim().replace(/\n/g, '<br>')}</span></li>`
  })
  html += '</ul>'
  return html
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
            class="bg-white rounded-xl shadow-2xl max-w-2xl w-full overflow-hidden"
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
                v-html="formattedMessage"
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

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { XMarkIcon } from '@heroicons/vue/24/outline'

const visible = ref(false)

const shortcuts = [
  { keys: ['G'], description: 'Gehe zum Dashboard' },
  { keys: ['I'], description: 'Gehe zu Nachrichten' },
  { keys: ['S'], description: 'Gehe zu Quellen' },
  { keys: ['R'], description: 'Gehe zu Regeln' },
  { keys: [','], description: 'Gehe zu Einstellungen' },
  { keys: ['?'], description: 'Diese Hilfe anzeigen' },
  { keys: ['J'], description: 'Nächster Eintrag' },
  { keys: ['K'], description: 'Vorheriger Eintrag' },
  { keys: ['Enter'], description: 'Eintrag öffnen' },
  { keys: ['M'], description: 'Als gelesen markieren' },
  { keys: ['Entf'], description: 'Archivieren' },
  { keys: ['Esc'], description: 'Schließen / Zurück' }
]

const showHelp = () => {
  visible.value = true
}

const hideHelp = () => {
  visible.value = false
}

onMounted(() => {
  window.addEventListener('show-shortcuts-help', showHelp)
})

onUnmounted(() => {
  window.removeEventListener('show-shortcuts-help', showHelp)
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="visible"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50 p-4"
      @click.self="hideHelp"
      @keydown.escape="hideHelp"
    >
      <div class="w-full max-w-md rounded-lg bg-white shadow-xl">
        <div class="flex items-center justify-between border-b border-gray-200 p-4">
          <h2 class="text-lg font-medium text-gray-900">Tastaturkürzel</h2>
          <button
            type="button"
            class="text-gray-400 hover:text-gray-600"
            @click="hideHelp"
          >
            <XMarkIcon class="h-5 w-5" />
          </button>
        </div>

        <div class="max-h-96 overflow-y-auto p-4">
          <table class="w-full">
            <tbody class="divide-y divide-gray-100">
              <tr v-for="shortcut in shortcuts" :key="shortcut.description">
                <td class="py-2 pr-4">
                  <div class="flex gap-1">
                    <kbd
                      v-for="key in shortcut.keys"
                      :key="key"
                      class="rounded bg-gray-100 px-2 py-1 font-mono text-sm text-gray-700"
                    >
                      {{ key }}
                    </kbd>
                  </div>
                </td>
                <td class="py-2 text-sm text-gray-600">
                  {{ shortcut.description }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="border-t border-gray-200 p-4">
          <p class="text-xs text-gray-500">
            Drücke <kbd class="rounded bg-gray-100 px-1 font-mono">?</kbd> um diese Hilfe jederzeit anzuzeigen
          </p>
        </div>
      </div>
    </div>
  </Teleport>
</template>

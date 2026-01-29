<script setup lang="ts">
import { RouterView } from 'vue-router'
import AppLayout from './components/AppLayout.vue'
import KeyboardShortcutsHelp from './components/KeyboardShortcutsHelp.vue'
import MOTDModal from './components/MOTDModal.vue'
import { useGlobalShortcuts } from './composables/useKeyboardShortcuts'

useGlobalShortcuts()
</script>

<template>
  <AppLayout>
    <RouterView />
  </AppLayout>
  <KeyboardShortcutsHelp />
  <MOTDModal />
</template>

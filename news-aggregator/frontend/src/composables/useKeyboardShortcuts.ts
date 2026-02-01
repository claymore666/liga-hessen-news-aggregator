import { onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'

export interface Shortcut {
  key: string
  ctrl?: boolean
  shift?: boolean
  alt?: boolean
  description: string
  action: () => void
}

export function useKeyboardShortcuts(shortcuts: Shortcut[]) {
  const handleKeyDown = (event: KeyboardEvent) => {
    // Ignore if user is typing in an input
    if (
      event.target instanceof HTMLInputElement ||
      event.target instanceof HTMLTextAreaElement ||
      event.target instanceof HTMLSelectElement
    ) {
      return
    }

    for (const shortcut of shortcuts) {
      const keyMatch = event.key.toLowerCase() === shortcut.key.toLowerCase()
      const ctrlMatch = shortcut.ctrl ? event.ctrlKey || event.metaKey : !event.ctrlKey && !event.metaKey
      const shiftMatch = shortcut.shift ? event.shiftKey : !event.shiftKey
      const altMatch = shortcut.alt ? event.altKey : !event.altKey

      if (keyMatch && ctrlMatch && shiftMatch && altMatch) {
        event.preventDefault()
        shortcut.action()
        return
      }
    }
  }

  onMounted(() => {
    window.addEventListener('keydown', handleKeyDown)
  })

  onUnmounted(() => {
    window.removeEventListener('keydown', handleKeyDown)
  })
}

export function useGlobalShortcuts() {
  const router = useRouter()

  const shortcuts: Shortcut[] = [
    {
      key: 'g',
      description: 'Gehe zur Übersicht',
      action: () => router.push('/uebersicht')
    },
    {
      key: 'i',
      description: 'Gehe zu Nachrichten',
      action: () => router.push('/items')
    },
    {
      key: 's',
      description: 'Gehe zu Quellen',
      action: () => router.push('/sources')
    },
    {
      key: 'r',
      description: 'Gehe zu Regeln',
      action: () => router.push('/rules')
    },
    {
      key: ',',
      description: 'Gehe zu Einstellungen',
      action: () => router.push('/settings')
    },
    {
      key: '?',
      shift: true,
      description: 'Tastaturkürzel anzeigen',
      action: () => {
        window.dispatchEvent(new CustomEvent('show-shortcuts-help'))
      }
    }
  ]

  useKeyboardShortcuts(shortcuts)

  return { shortcuts }
}

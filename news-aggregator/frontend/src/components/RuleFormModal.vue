<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRulesStore } from '@/stores'
import { XMarkIcon } from '@heroicons/vue/24/outline'
import type { Rule, RuleType, Priority } from '@/types'

const props = defineProps<{
  rule?: Rule | null
}>()

const emit = defineEmits<{
  close: []
  saved: []
}>()

const rulesStore = useRulesStore()
const loading = ref(false)

const form = ref({
  name: props.rule?.name ?? '',
  description: props.rule?.description ?? '',
  rule_type: (props.rule?.rule_type ?? 'keyword') as RuleType,
  pattern: props.rule?.pattern ?? '',
  priority_boost: props.rule?.priority_boost ?? 10,
  target_priority: props.rule?.target_priority ?? null as Priority | null,
  enabled: props.rule?.enabled ?? true,
  order: props.rule?.order ?? 0
})

const isEditing = computed(() => !!props.rule)

const ruleTypes: { value: RuleType; label: string; description: string }[] = [
  {
    value: 'keyword',
    label: 'Schlüsselwort',
    description: 'Komma-getrennte Liste von Schlüsselwörtern (Groß-/Kleinschreibung ignoriert)'
  },
  {
    value: 'regex',
    label: 'Regulärer Ausdruck',
    description: 'Python-kompatibler regulärer Ausdruck'
  },
  {
    value: 'semantic',
    label: 'Semantisch (LLM)',
    description: 'Frage an das LLM, die mit JA/NEIN beantwortet wird'
  }
]

const priorities: { value: Priority | null; label: string }[] = [
  { value: null, label: 'Keine Änderung' },
  { value: 'high', label: 'Hoch' },
  { value: 'medium', label: 'Mittel' },
  { value: 'low', label: 'Niedrig' },
  { value: 'none', label: 'Keine' }
]

const save = async () => {
  loading.value = true
  try {
    if (isEditing.value && props.rule) {
      await rulesStore.updateRule(props.rule.id, form.value)
    } else {
      await rulesStore.createRule(form.value)
    }
    emit('saved')
  } finally {
    loading.value = false
  }
}

const currentRuleType = computed(() =>
  ruleTypes.find((t) => t.value === form.value.rule_type)
)
</script>

<template>
  <div class="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50 p-4">
    <div class="w-full max-w-lg rounded-lg bg-white shadow-xl">
      <div class="flex items-center justify-between border-b border-gray-200 p-4">
        <h2 class="text-lg font-medium text-gray-900">
          {{ isEditing ? 'Regel bearbeiten' : 'Neue Regel' }}
        </h2>
        <button
          type="button"
          class="text-gray-400 hover:text-gray-600"
          @click="emit('close')"
        >
          <XMarkIcon class="h-5 w-5" />
        </button>
      </div>

      <form class="max-h-[70vh] overflow-y-auto p-4" @submit.prevent="save">
        <div class="space-y-4">
          <div>
            <label class="block text-sm font-medium text-gray-700">Name</label>
            <input
              v-model="form.name"
              type="text"
              class="input mt-1"
              required
              placeholder="z.B. Haushaltskürzungen"
            />
          </div>

          <div>
            <label class="block text-sm font-medium text-gray-700">Beschreibung</label>
            <textarea
              v-model="form.description"
              class="input mt-1"
              rows="2"
              placeholder="Optionale Beschreibung der Regel"
            />
          </div>

          <div>
            <label class="block text-sm font-medium text-gray-700">Regeltyp</label>
            <select v-model="form.rule_type" class="input mt-1">
              <option v-for="t in ruleTypes" :key="t.value" :value="t.value">
                {{ t.label }}
              </option>
            </select>
            <p v-if="currentRuleType" class="mt-1 text-xs text-gray-500">
              {{ currentRuleType.description }}
            </p>
          </div>

          <div>
            <label class="block text-sm font-medium text-gray-700">
              {{ form.rule_type === 'semantic' ? 'Frage' : 'Muster' }}
            </label>
            <textarea
              v-model="form.pattern"
              class="input mt-1 font-mono text-sm"
              rows="3"
              required
              :placeholder="form.rule_type === 'keyword' ? 'kürzung, streichung, haushaltssperre' : form.rule_type === 'regex' ? 'haushalts?(kürzung|sperre)' : 'Behandelt der Artikel Haushaltskürzungen im Sozialbereich?'"
            />
          </div>

          <div class="grid gap-4 sm:grid-cols-2">
            <div>
              <label class="block text-sm font-medium text-gray-700">
                Prioritäts-Boost
              </label>
              <input
                v-model.number="form.priority_boost"
                type="number"
                min="-50"
                max="50"
                class="input mt-1"
              />
              <p class="mt-1 text-xs text-gray-500">
                Punkte die zur Priorität addiert werden (-50 bis +50)
              </p>
            </div>

            <div>
              <label class="block text-sm font-medium text-gray-700">
                Ziel-Priorität
              </label>
              <select v-model="form.target_priority" class="input mt-1">
                <option v-for="p in priorities" :key="String(p.value)" :value="p.value">
                  {{ p.label }}
                </option>
              </select>
              <p class="mt-1 text-xs text-gray-500">
                Überschreibt die berechnete Priorität
              </p>
            </div>
          </div>

          <div class="flex items-center gap-2">
            <input
              v-model="form.enabled"
              type="checkbox"
              class="rounded border-gray-300"
            />
            <label class="text-sm text-gray-700">Regel aktivieren</label>
          </div>
        </div>

        <div class="mt-6 flex justify-end gap-3">
          <button
            type="button"
            class="btn btn-secondary"
            @click="emit('close')"
          >
            Abbrechen
          </button>
          <button
            type="submit"
            class="btn btn-primary"
            :disabled="loading"
          >
            {{ loading ? 'Speichere...' : (isEditing ? 'Speichern' : 'Erstellen') }}
          </button>
        </div>
      </form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRulesStore } from '@/stores'
import {
  PlusIcon,
  PencilIcon,
  TrashIcon,
  CheckCircleIcon,
  XCircleIcon,
  ArrowsUpDownIcon
} from '@heroicons/vue/24/outline'
import RuleFormModal from '@/components/RuleFormModal.vue'
import type { Rule, RuleType } from '@/types'

const rulesStore = useRulesStore()
const showCreateModal = ref(false)
const editingRule = ref<Rule | null>(null)

const ruleTypeLabels: Record<RuleType, string> = {
  keyword: 'Schlüsselwort',
  regex: 'Regex',
  semantic: 'Semantisch (LLM)'
}

const deleteRule = async (rule: Rule) => {
  if (confirm(`Regel "${rule.name}" wirklich löschen?`)) {
    await rulesStore.deleteRule(rule.id)
  }
}

const onRuleSaved = () => {
  showCreateModal.value = false
  editingRule.value = null
}

onMounted(() => {
  rulesStore.fetchRules()
})
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-bold text-gray-900">Regeln</h1>
        <p class="text-sm text-gray-500">
          {{ rulesStore.rules.length }} Regeln konfiguriert
        </p>
      </div>
      <button
        type="button"
        class="btn btn-primary"
        @click="showCreateModal = true"
      >
        <PlusIcon class="mr-2 h-4 w-4" />
        Neue Regel
      </button>
    </div>

    <div v-if="rulesStore.loading" class="card py-12 text-center">
      <p class="text-gray-500">Lade Regeln...</p>
    </div>

    <div v-else-if="rulesStore.rules.length === 0" class="card py-12 text-center">
      <p class="text-gray-500">Keine Regeln konfiguriert</p>
      <button
        type="button"
        class="btn btn-primary mt-4"
        @click="showCreateModal = true"
      >
        Erste Regel erstellen
      </button>
    </div>

    <div v-else class="space-y-3">
      <div
        v-for="rule in rulesStore.rules"
        :key="rule.id"
        class="card"
      >
        <div class="flex items-start justify-between gap-4">
          <div class="flex items-start gap-3">
            <button
              type="button"
              class="mt-1 cursor-move text-gray-400 hover:text-gray-600"
            >
              <ArrowsUpDownIcon class="h-5 w-5" />
            </button>
            <div>
              <div class="flex items-center gap-2">
                <component
                  :is="rule.enabled ? CheckCircleIcon : XCircleIcon"
                  class="h-5 w-5"
                  :class="rule.enabled ? 'text-green-500' : 'text-gray-400'"
                />
                <h3 class="font-medium text-gray-900">{{ rule.name }}</h3>
              </div>
              <p v-if="rule.description" class="mt-1 text-sm text-gray-500">
                {{ rule.description }}
              </p>
              <div class="mt-2 flex flex-wrap gap-2">
                <span class="badge bg-liga-100 text-liga-700">
                  {{ ruleTypeLabels[rule.rule_type] }}
                </span>
                <span
                  class="badge"
                  :class="rule.priority_boost > 0 ? 'bg-green-100 text-green-700' : rule.priority_boost < 0 ? 'bg-red-100 text-red-700' : 'bg-gray-100 text-gray-700'"
                >
                  {{ rule.priority_boost > 0 ? '+' : '' }}{{ rule.priority_boost }} Priorität
                </span>
                <span v-if="rule.target_priority" class="badge bg-orange-100 text-orange-700">
                  Setzt: {{ rule.target_priority }}
                </span>
              </div>
              <p class="mt-2 rounded bg-gray-50 px-2 py-1 font-mono text-xs text-gray-600">
                {{ rule.pattern }}
              </p>
            </div>
          </div>

          <div class="flex gap-2">
            <button
              type="button"
              class="btn btn-secondary text-sm"
              @click="editingRule = rule"
            >
              <PencilIcon class="h-4 w-4" />
            </button>
            <button
              type="button"
              class="btn btn-danger text-sm"
              @click="deleteRule(rule)"
            >
              <TrashIcon class="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Create/Edit Modal -->
    <RuleFormModal
      v-if="showCreateModal || editingRule"
      :rule="editingRule"
      @close="showCreateModal = false; editingRule = null"
      @saved="onRuleSaved"
    />
  </div>
</template>

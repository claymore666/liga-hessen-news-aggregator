import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { rulesApi } from '@/api'
import type { Rule } from '@/types'

export const useRulesStore = defineStore('rules', () => {
  const rules = ref<Rule[]>([])
  const currentRule = ref<Rule | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  const enabledRules = computed(() => rules.value.filter((r) => r.enabled))

  const rulesByType = computed(() => {
    const grouped: Record<string, Rule[]> = {}
    rules.value.forEach((rule) => {
      if (!grouped[rule.rule_type]) {
        grouped[rule.rule_type] = []
      }
      grouped[rule.rule_type].push(rule)
    })
    return grouped
  })

  async function fetchRules() {
    loading.value = true
    error.value = null
    try {
      const response = await rulesApi.list()
      rules.value = response.data
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch rules'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function fetchRule(id: number) {
    loading.value = true
    error.value = null
    try {
      const response = await rulesApi.get(id)
      currentRule.value = response.data
      return response.data
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch rule'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function createRule(data: Partial<Rule>) {
    loading.value = true
    error.value = null
    try {
      const response = await rulesApi.create(data)
      rules.value.push(response.data)
      return response.data
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to create rule'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function updateRule(id: number, data: Partial<Rule>) {
    loading.value = true
    error.value = null
    try {
      const response = await rulesApi.update(id, data)
      const index = rules.value.findIndex((r) => r.id === id)
      if (index !== -1) rules.value[index] = response.data
      if (currentRule.value?.id === id) currentRule.value = response.data
      return response.data
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to update rule'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function deleteRule(id: number) {
    loading.value = true
    error.value = null
    try {
      await rulesApi.delete(id)
      rules.value = rules.value.filter((r) => r.id !== id)
      if (currentRule.value?.id === id) currentRule.value = null
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to delete rule'
      throw e
    } finally {
      loading.value = false
    }
  }

  async function testRule(id: number, content: string) {
    error.value = null
    try {
      const response = await rulesApi.test(id, content)
      return response.data
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to test rule'
      throw e
    }
  }

  function reorderRules(newOrder: number[]) {
    const reordered = newOrder.map((id) => rules.value.find((r) => r.id === id)!)
    rules.value = reordered
  }

  return {
    rules,
    currentRule,
    loading,
    error,
    enabledRules,
    rulesByType,
    fetchRules,
    fetchRule,
    createRule,
    updateRule,
    deleteRule,
    testRule,
    reorderRules
  }
})

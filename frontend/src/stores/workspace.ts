import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { createKnowledgeBase as createRequest, listKnowledgeBases } from '../api/knowledgeBases'
import type { CreateKnowledgeBaseInput } from '../api/knowledgeBases'
import type { DocumentTask, KnowledgeBase, QuestionResponse } from '../types/api'

export const useWorkspaceStore = defineStore('workspace', () => {
  const knowledgeBases = ref<KnowledgeBase[]>([])
  const activeKnowledgeBaseId = ref<string | null>(null)
  const documents = ref<Record<string, DocumentTask[]>>({})
  const answer = ref<QuestionResponse | null>(null)
  const loadingKnowledgeBases = ref(false)
  const activeKnowledgeBase = computed(() =>
    knowledgeBases.value.find((item) => item.id === activeKnowledgeBaseId.value) ?? null)
  const activeDocuments = computed(() => activeKnowledgeBaseId.value
    ? documents.value[activeKnowledgeBaseId.value] ?? [] : [])

  async function loadKnowledgeBases() {
    loadingKnowledgeBases.value = true
    try {
      knowledgeBases.value = await listKnowledgeBases()
      if (!activeKnowledgeBaseId.value && knowledgeBases.value.length)
        activeKnowledgeBaseId.value = knowledgeBases.value[0]!.id
    } finally { loadingKnowledgeBases.value = false }
  }

  async function createKnowledgeBase(input: CreateKnowledgeBaseInput) {
    const created = await createRequest(input)
    knowledgeBases.value.push(created)
    activeKnowledgeBaseId.value = created.id
    return created
  }

  function selectKnowledgeBase(id: string) {
    activeKnowledgeBaseId.value = id
    answer.value = null
  }

  return {
    knowledgeBases, activeKnowledgeBaseId, documents, answer, loadingKnowledgeBases,
    activeKnowledgeBase, activeDocuments, loadKnowledgeBases, createKnowledgeBase,
    selectKnowledgeBase,
  }
})

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { pollDocumentStatus, uploadDocument } from '../api/documents'
import { createKnowledgeBase as createRequest, listKnowledgeBases } from '../api/knowledgeBases'
import { askQuestion } from '../api/questions'
import { ApiError } from '../api/client'
import type { CreateKnowledgeBaseInput } from '../api/knowledgeBases'
import type { DocumentTask, KnowledgeBase, QuestionResponse } from '../types/api'

export const useWorkspaceStore = defineStore('workspace', () => {
  const knowledgeBases = ref<KnowledgeBase[]>([])
  const activeKnowledgeBaseId = ref<string | null>(null)
  const documents = ref<Record<string, DocumentTask[]>>({})
  const answer = ref<QuestionResponse | null>(null)
  const asking = ref(false)
  const loadingKnowledgeBases = ref(false)
  const activeKnowledgeBase = computed(() =>
    knowledgeBases.value.find((item) => item.id === activeKnowledgeBaseId.value) ?? null)
  const activeDocuments = computed(() => activeKnowledgeBaseId.value
    ? documents.value[activeKnowledgeBaseId.value] ?? [] : [])
  let generation = 0

  function reset() {
    generation += 1
    knowledgeBases.value = []
    activeKnowledgeBaseId.value = null
    documents.value = {}
    answer.value = null
    asking.value = false
    loadingKnowledgeBases.value = false
  }

  async function loadKnowledgeBases() {
    const operationGeneration = generation
    loadingKnowledgeBases.value = true
    try {
      const loaded = await listKnowledgeBases()
      if (operationGeneration !== generation) return
      knowledgeBases.value = loaded
      if (!activeKnowledgeBaseId.value && knowledgeBases.value.length)
        activeKnowledgeBaseId.value = knowledgeBases.value[0]!.id
    } finally {
      if (operationGeneration === generation) loadingKnowledgeBases.value = false
    }
  }

  async function createKnowledgeBase(input: CreateKnowledgeBaseInput) {
    const operationGeneration = generation
    const created = await createRequest(input)
    if (operationGeneration !== generation) return created
    knowledgeBases.value.push(created)
    activeKnowledgeBaseId.value = created.id
    answer.value = null
    return created
  }

  function selectKnowledgeBase(id: string) {
    activeKnowledgeBaseId.value = id
    answer.value = null
  }

  async function uploadAndTrackDocument(file: File) {
    const operationGeneration = generation
    const id = activeKnowledgeBaseId.value
    if (!id) throw new Error('请先选择知识库。')
    const pending = { ...await uploadDocument(id, file), file_name: file.name }
    if (operationGeneration !== generation) return pending
    documents.value[id] = [pending, ...(documents.value[id] ?? [])]
    try {
      const finished = { ...await pollDocumentStatus(pending.document_id), file_name: file.name }
      if (operationGeneration !== generation) return finished
      documents.value[id] = documents.value[id].map((item) =>
        item.document_id === finished.document_id ? finished : item)
      return finished
    } catch (error) {
      if (operationGeneration !== generation) throw error
      const failed: DocumentTask = {
        ...pending,
        status: 'failed',
        error_code: error instanceof ApiError ? error.code : 'DOCUMENT_POLL_FAILED',
        error_message: error instanceof Error ? error.message : '文档状态查询失败。',
      }
      documents.value[id] = documents.value[id].map((item) =>
        item.document_id === pending.document_id ? failed : item)
      throw error
    }
  }

  async function submitQuestion(question: string) {
    const operationGeneration = generation
    const knowledgeBaseId = activeKnowledgeBaseId.value
    if (!knowledgeBaseId) throw new Error('请先选择知识库。')
    asking.value = true
    try {
      const result = await askQuestion(knowledgeBaseId, question.trim(), 5)
      if (operationGeneration === generation && activeKnowledgeBaseId.value === knowledgeBaseId)
        answer.value = result
      return result
    } finally {
      if (operationGeneration === generation) asking.value = false
    }
  }

  return {
    knowledgeBases, activeKnowledgeBaseId, documents, answer, asking, loadingKnowledgeBases,
    activeKnowledgeBase, activeDocuments, loadKnowledgeBases, createKnowledgeBase,
    selectKnowledgeBase, uploadAndTrackDocument, submitQuestion, reset,
  }
})

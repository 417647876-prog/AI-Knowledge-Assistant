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
    answer.value = null
    return created
  }

  function selectKnowledgeBase(id: string) {
    activeKnowledgeBaseId.value = id
    answer.value = null
  }

  async function uploadAndTrackDocument(file: File) {
    const id = activeKnowledgeBaseId.value
    if (!id) throw new Error('请先选择知识库。')
    const pending = { ...await uploadDocument(id, file), file_name: file.name }
    documents.value[id] = [pending, ...(documents.value[id] ?? [])]
    try {
      const finished = { ...await pollDocumentStatus(pending.document_id), file_name: file.name }
      documents.value[id] = documents.value[id].map((item) =>
        item.document_id === finished.document_id ? finished : item)
      return finished
    } catch (error) {
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
    const knowledgeBaseId = activeKnowledgeBaseId.value
    if (!knowledgeBaseId) throw new Error('请先选择知识库。')
    asking.value = true
    try {
      const result = await askQuestion(knowledgeBaseId, question.trim(), 5)
      if (activeKnowledgeBaseId.value === knowledgeBaseId) answer.value = result
      return result
    } finally { asking.value = false }
  }

  return {
    knowledgeBases, activeKnowledgeBaseId, documents, answer, asking, loadingKnowledgeBases,
    activeKnowledgeBase, activeDocuments, loadKnowledgeBases, createKnowledgeBase,
    selectKnowledgeBase, uploadAndTrackDocument, submitQuestion,
  }
})

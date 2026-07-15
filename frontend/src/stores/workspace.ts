import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import {
  deleteDocument as deleteRequest,
  listDocuments,
  pollDocumentStatus,
  reprocessDocument as reprocessRequest,
  uploadDocument,
} from '../api/documents'
import { createKnowledgeBase as createRequest, listKnowledgeBases } from '../api/knowledgeBases'
import type { CreateKnowledgeBaseInput } from '../api/knowledgeBases'
import type { DocumentTask, KnowledgeBase } from '../types/api'

export const useWorkspaceStore = defineStore('workspace', () => {
  const knowledgeBases = ref<KnowledgeBase[]>([])
  const activeKnowledgeBaseId = ref<string | null>(null)
  const documents = ref<Record<string, DocumentTask[]>>({})
  const loadingKnowledgeBases = ref(false)
  const loadingDocuments = ref(false)
  const activeKnowledgeBase = computed(() =>
    knowledgeBases.value.find((item) => item.id === activeKnowledgeBaseId.value) ?? null)
  const activeDocuments = computed(() => activeKnowledgeBaseId.value
    ? documents.value[activeKnowledgeBaseId.value] ?? [] : [])
  let generation = 0
  let documentLoadSequence = 0
  const pollingDocuments = new Map<string, symbol>()

  const isProcessing = (status: DocumentTask['status']) =>
    status === 'pending' || status === 'parsing' || status === 'embedding'

  function replaceDocument(knowledgeBaseId: string, document: DocumentTask) {
    documents.value[knowledgeBaseId] = (documents.value[knowledgeBaseId] ?? []).map((item) =>
      item.document_id === document.document_id ? document : item)
  }

  async function trackDocument(knowledgeBaseId: string, pending: DocumentTask) {
    if (!isProcessing(pending.status) || pollingDocuments.has(pending.document_id)) return pending
    const operationGeneration = generation
    const pollingToken = Symbol(pending.document_id)
    pollingDocuments.set(pending.document_id, pollingToken)
    try {
      const finished = await pollDocumentStatus(pending.document_id)
      if (operationGeneration === generation && activeKnowledgeBaseId.value === knowledgeBaseId)
        replaceDocument(knowledgeBaseId, finished)
      return finished
    } finally {
      if (pollingDocuments.get(pending.document_id) === pollingToken)
        pollingDocuments.delete(pending.document_id)
    }
  }

  function reset() {
    generation += 1
    knowledgeBases.value = []
    activeKnowledgeBaseId.value = null
    documents.value = {}
    loadingKnowledgeBases.value = false
    loadingDocuments.value = false
    documentLoadSequence += 1
    pollingDocuments.clear()
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
    return created
  }

  async function loadDocuments() {
    const operationGeneration = generation
    const knowledgeBaseId = activeKnowledgeBaseId.value
    if (!knowledgeBaseId) return
    const loadSequence = ++documentLoadSequence
    loadingDocuments.value = true
    try {
      const loaded = await listDocuments(knowledgeBaseId)
      if (
        operationGeneration !== generation
        || activeKnowledgeBaseId.value !== knowledgeBaseId
        || loadSequence !== documentLoadSequence
      ) return
      documents.value[knowledgeBaseId] = loaded
      for (const document of loaded) {
        if (isProcessing(document.status)) void trackDocument(knowledgeBaseId, document).catch(() => {})
      }
    } finally {
      if (
        operationGeneration === generation
        && activeKnowledgeBaseId.value === knowledgeBaseId
        && loadSequence === documentLoadSequence
      )
        loadingDocuments.value = false
    }
  }

  function selectKnowledgeBase(id: string) {
    activeKnowledgeBaseId.value = id
  }

  async function uploadAndTrackDocument(file: File) {
    const operationGeneration = generation
    const id = activeKnowledgeBaseId.value
    if (!id) throw new Error('请先选择知识库。')
    const pending = await uploadDocument(id, file)
    if (operationGeneration !== generation) return pending
    documents.value[id] = [pending, ...(documents.value[id] ?? [])]
    return trackDocument(id, pending)
  }

  async function reprocessDocument(documentId: string) {
    const operationGeneration = generation
    const knowledgeBaseId = activeKnowledgeBaseId.value
    if (!knowledgeBaseId) throw new Error('请先选择知识库。')
    const pending = await reprocessRequest(documentId)
    if (operationGeneration !== generation || activeKnowledgeBaseId.value !== knowledgeBaseId) return pending
    replaceDocument(knowledgeBaseId, pending)
    return trackDocument(knowledgeBaseId, pending)
  }

  async function deleteDocument(documentId: string) {
    const operationGeneration = generation
    const knowledgeBaseId = activeKnowledgeBaseId.value
    if (!knowledgeBaseId) throw new Error('请先选择知识库。')
    await deleteRequest(documentId)
    if (operationGeneration !== generation || activeKnowledgeBaseId.value !== knowledgeBaseId) return
    documents.value[knowledgeBaseId] = (documents.value[knowledgeBaseId] ?? []).filter(
      (item) => item.document_id !== documentId,
    )
  }

  return {
    knowledgeBases, activeKnowledgeBaseId, documents, loadingKnowledgeBases, loadingDocuments,
    activeKnowledgeBase, activeDocuments, loadKnowledgeBases, createKnowledgeBase,
    selectKnowledgeBase, loadDocuments, uploadAndTrackDocument, reprocessDocument, deleteDocument,
    reset,
  }
})

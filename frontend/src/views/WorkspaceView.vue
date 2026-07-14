<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { formatApiError } from '../api/client'
import DocumentTable from '../components/DocumentTable.vue'
import DocumentUpload from '../components/DocumentUpload.vue'
import KnowledgeBaseSidebar from '../components/KnowledgeBaseSidebar.vue'
import QuestionPanel from '../components/QuestionPanel.vue'
import { useWorkspaceStore } from '../stores/workspace'

const store = useWorkspaceStore()
const knowledgeBaseLoadError = ref<string | null>(null)
const documentLoadError = ref<string | null>(null)

async function loadKnowledgeBases() {
  knowledgeBaseLoadError.value = null
  try {
    await store.loadKnowledgeBases()
  } catch (error) {
    knowledgeBaseLoadError.value = formatApiError(error)
  }
}

onMounted(loadKnowledgeBases)

async function loadDocuments() {
  documentLoadError.value = null
  try {
    await store.loadDocuments()
  } catch (error) {
    documentLoadError.value = formatApiError(error)
  }
}

watch(() => store.activeKnowledgeBaseId, (knowledgeBaseId) => {
  if (knowledgeBaseId) void loadDocuments()
}, { immediate: true })
</script>

<template>
  <main class="workspace-page">
    <div class="workspace-layout">
      <aside class="knowledge-sidebar">
        <KnowledgeBaseSidebar />
      </aside>

      <section class="workspace-main">
        <section
          v-if="knowledgeBaseLoadError"
          data-test="knowledge-base-load-error"
          class="workspace-empty workspace-card"
        >
          <p>{{ knowledgeBaseLoadError }}</p>
          <el-button
            data-test="reload-knowledge-bases"
            type="primary"
            :loading="store.loadingKnowledgeBases"
            @click="loadKnowledgeBases"
          >
            重新加载
          </el-button>
        </section>
        <template v-else-if="store.activeKnowledgeBase">
          <section class="workspace-card">
            <DocumentUpload />
            <el-alert
              v-if="documentLoadError"
              data-test="document-load-error"
              type="error"
              :title="documentLoadError"
              show-icon
              :closable="false"
            />
            <el-button v-if="documentLoadError" link type="primary" @click="loadDocuments">
              重新加载文档
            </el-button>
            <DocumentTable />
          </section>
          <section class="workspace-card">
            <QuestionPanel />
          </section>
        </template>
        <section v-else class="workspace-empty workspace-card">
          请选择或创建知识库
        </section>
      </section>
    </div>
  </main>
</template>

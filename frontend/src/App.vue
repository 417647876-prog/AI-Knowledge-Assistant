<script setup lang="ts">
import { onMounted } from 'vue'
import DocumentTable from './components/DocumentTable.vue'
import DocumentUpload from './components/DocumentUpload.vue'
import KnowledgeBaseSidebar from './components/KnowledgeBaseSidebar.vue'
import QuestionPanel from './components/QuestionPanel.vue'
import { useWorkspaceStore } from './stores/workspace'

const store = useWorkspaceStore()

onMounted(() => store.loadKnowledgeBases())
</script>

<template>
  <main class="app-shell">
    <header class="app-header"><h1>AI 知识库助手</h1></header>
    <div class="workspace-layout">
      <aside class="knowledge-sidebar">
        <KnowledgeBaseSidebar />
      </aside>

      <section class="workspace-main">
        <template v-if="store.activeKnowledgeBase">
          <section class="workspace-card">
            <DocumentUpload />
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

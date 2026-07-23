<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { formatApiError } from '../api/client'
import KnowledgeBaseSidebar from '../components/KnowledgeBaseSidebar.vue'
import { useWorkspaceStore } from '../stores/workspace'

const store = useWorkspaceStore()
const error = ref<string | null>(null)

async function load(): Promise<void> {
  error.value = null
  try { await store.loadKnowledgeBases() } catch (reason) { error.value = formatApiError(reason) }
}

onMounted(() => { void load() })
</script>

<template>
  <main class="knowledge-bases-page">
    <section v-if="error" class="workspace-card workspace-empty" data-test="knowledge-base-load-error">
      <p>{{ error }}</p><el-button type="primary" @click="load">重新加载</el-button>
    </section>
    <KnowledgeBaseSidebar v-else />
    <p v-if="!error && !store.loadingKnowledgeBases && !store.knowledgeBases.length" class="workspace-empty workspace-card">创建一个知识库后即可上传和提问。</p>
  </main>
</template>

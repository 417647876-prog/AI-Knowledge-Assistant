<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { formatApiError } from '../api/client'
import DocumentTable from '../components/DocumentTable.vue'
import DocumentUpload from '../components/DocumentUpload.vue'
import QuestionPanel from '../components/QuestionPanel.vue'
import SupportGrantDialog from '../components/SupportGrantDialog.vue'
import { useAuthStore } from '../stores/auth'
import { useConversationsStore } from '../stores/conversations'
import { useWorkspaceStore } from '../stores/workspace'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const conversations = useConversationsStore()
const store = useWorkspaceStore()
const error = ref<string | null>(null)
const conversationError = ref<string | null>(null)
const supportVisible = ref(false)
const knowledgeBaseId = computed(() => String(route.params.knowledgeBaseId ?? ''))
let conversationActivationSequence = 0

async function load(): Promise<void> {
  error.value = null
  try {
    if (!store.knowledgeBases.length) await store.loadKnowledgeBases()
    if (!store.knowledgeBases.some((item) => item.id === knowledgeBaseId.value)) {
      await router.replace('/')
      return
    }
    store.selectKnowledgeBase(knowledgeBaseId.value)
    await store.loadDocuments()
  } catch (reason) { error.value = formatApiError(reason) }
}

async function deleteKnowledgeBase(): Promise<void> {
  if (!store.activeKnowledgeBase) return
  try {
    await ElMessageBox.confirm(`确定删除知识库“${store.activeKnowledgeBase.name}”吗？可在回收站恢复。`, '删除知识库', { type: 'warning' })
    await store.deleteKnowledgeBase(store.activeKnowledgeBase.id)
    ElMessage.success('知识库已移入回收站。')
    await router.replace('/')
  } catch (reason) {
    if (reason !== 'cancel' && reason !== 'close') ElMessage.error(formatApiError(reason))
  }
}

async function activateConversations(
  userId: string,
  activeKnowledgeBaseId: string,
  force = false,
): Promise<void> {
  const sequence = ++conversationActivationSequence
  conversationError.value = null
  try {
    await conversations.activate(userId, activeKnowledgeBaseId, force)
  } catch (reason) {
    if (
      sequence === conversationActivationSequence
      && auth.user?.id === userId
      && store.activeKnowledgeBaseId === activeKnowledgeBaseId
    ) conversationError.value = formatApiError(reason)
  }
}

function retryConversationLoad(): void {
  const userId = auth.user?.id
  const activeKnowledgeBaseId = store.activeKnowledgeBaseId
  if (userId && activeKnowledgeBaseId) {
    void activateConversations(userId, activeKnowledgeBaseId, true)
  }
}

watch(knowledgeBaseId, () => { void load() })
watch(
  [() => auth.user?.id, () => store.activeKnowledgeBaseId],
  ([userId, activeKnowledgeBaseId]) => {
    if (userId && activeKnowledgeBaseId) {
      void activateConversations(userId, activeKnowledgeBaseId)
    }
  },
  { immediate: true },
)
onMounted(() => { void load() })
</script>

<template>
  <main class="documents-page">
    <section v-if="error" class="workspace-card workspace-empty" data-test="document-load-error"><p>{{ error }}</p><el-button type="primary" @click="load">重新加载</el-button></section>
    <template v-else-if="store.activeKnowledgeBase">
      <header class="page-toolbar workspace-card">
        <div><el-button link @click="router.push('/')">‹ 知识库</el-button><h2>{{ store.activeKnowledgeBase.name }}</h2></div>
        <div class="page-toolbar-actions"><router-link to="/trash">回收站</router-link><el-button @click="supportVisible = true">支持授权</el-button><el-button type="danger" @click="deleteKnowledgeBase">删除知识库</el-button></div>
      </header>
      <section class="workspace-card"><DocumentUpload /><DocumentTable /></section>
      <section class="workspace-card">
        <el-alert
          v-if="conversationError"
          data-test="conversation-load-error"
          type="error"
          :title="conversationError"
          show-icon
          :closable="false"
        />
        <el-button
          v-if="conversationError"
          data-test="reload-conversations"
          link
          type="primary"
          @click="retryConversationLoad"
        >
          重新加载会话
        </el-button>
        <QuestionPanel />
      </section>
      <SupportGrantDialog v-model="supportVisible" :knowledge-base-id="store.activeKnowledgeBase.id" />
    </template>
  </main>
</template>

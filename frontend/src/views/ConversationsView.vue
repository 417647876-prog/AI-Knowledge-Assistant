<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { formatApiError } from '../api/client'
import ConversationList from '../components/ConversationList.vue'
import QuestionPanel from '../components/QuestionPanel.vue'
import { useAuthStore } from '../stores/auth'
import { useConversationsStore } from '../stores/conversations'
import { useWorkspaceStore } from '../stores/workspace'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const conversations = useConversationsStore()
const workspace = useWorkspaceStore()
const pageError = ref<string | null>(null)
const switchingId = ref<string | null>(null)
const deletingId = ref<string | null>(null)
const structuralOperation = computed(() =>
  conversations.creating || conversations.clearing || deletingId.value !== null,
)
const knowledgeBaseId = computed(() => String(route.params.knowledgeBaseId ?? ''))
const activeKnowledgeBase = computed(() => workspace.knowledgeBases.find(
  (item) => item.id === knowledgeBaseId.value && item.owner_id === auth.user?.id,
))
const conversationContextReady = computed(() =>
  conversations.activeUserId === auth.user?.id
  && conversations.activeKnowledgeBaseId === knowledgeBaseId.value,
)
// 即使旧请求迟到，页面也只渲染当前知识库的会话摘要。
const visibleConversations = computed(() => conversationContextReady.value
  ? conversations.conversations.filter((item) => item.knowledge_base_id === knowledgeBaseId.value)
  : [])
let loadSequence = 0
let openSequence = 0
let loadMoreSequence = 0

async function load(force = false): Promise<void> {
  const sequence = ++loadSequence
  const userId = auth.user?.id
  const requestedKnowledgeBaseId = knowledgeBaseId.value
  pageError.value = null
  if (!userId || !requestedKnowledgeBaseId) return

  try {
    if (!workspace.knowledgeBases.length) await workspace.loadKnowledgeBases()
    if (sequence !== loadSequence) return
    if (!workspace.knowledgeBases.some(
      (item) => item.id === requestedKnowledgeBaseId && item.owner_id === userId,
    )) {
      await router.replace('/')
      return
    }
    workspace.selectKnowledgeBase(requestedKnowledgeBaseId)
    await conversations.activate(userId, requestedKnowledgeBaseId, force)
  } catch (error) {
    if (
      sequence === loadSequence
      && auth.user?.id === userId
      && knowledgeBaseId.value === requestedKnowledgeBaseId
    ) pageError.value = formatApiError(error)
  }
}

async function openConversation(conversationId: string): Promise<void> {
  if (
    conversationId === conversations.activeConversationId
    || switchingId.value
    || structuralOperation.value
  ) return
  const sequence = ++openSequence
  const userId = auth.user?.id
  const requestedKnowledgeBaseId = knowledgeBaseId.value
  switchingId.value = conversationId
  pageError.value = null
  try {
    await conversations.openConversation(conversationId)
  } catch (error) {
    if (
      sequence === openSequence
      && auth.user?.id === userId
      && knowledgeBaseId.value === requestedKnowledgeBaseId
    ) pageError.value = formatApiError(error)
  } finally {
    if (sequence === openSequence) switchingId.value = null
  }
}

async function loadMoreConversations(): Promise<void> {
  if (structuralOperation.value || conversations.loading) return
  const sequence = ++loadMoreSequence
  const userId = auth.user?.id
  const requestedKnowledgeBaseId = knowledgeBaseId.value
  pageError.value = null
  try {
    await conversations.loadConversations(conversations.currentPage + 1)
  } catch (error) {
    if (
      sequence === loadMoreSequence
      && auth.user?.id === userId
      && knowledgeBaseId.value === requestedKnowledgeBaseId
    ) pageError.value = formatApiError(error)
  }
}

async function startNewConversation(): Promise<void> {
  if (structuralOperation.value || conversations.isStreaming) return
  pageError.value = null
  try {
    await conversations.newConversation()
  } catch (error) {
    pageError.value = formatApiError(error)
  }
}

async function deleteConversation(conversationId: string, title: string): Promise<void> {
  if (structuralOperation.value || conversations.isStreaming) return
  try {
    await ElMessageBox.confirm(`确定删除会话“${title}”吗？`, '删除会话', { type: 'warning' })
    deletingId.value = conversationId
    await conversations.deleteConversation(conversationId)
    ElMessage.success('会话已删除。')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') pageError.value = formatApiError(error)
  } finally {
    deletingId.value = null
  }
}

async function clearHistory(): Promise<void> {
  if (structuralOperation.value || conversations.isStreaming) return
  try {
    await ElMessageBox.confirm(
      '确定清空当前知识库的全部问答历史吗？',
      '清空历史',
      { type: 'warning' },
    )
    await conversations.clear()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') pageError.value = formatApiError(error)
  }
}

watch(
  [knowledgeBaseId, () => auth.user?.id],
  ([id, userId]) => {
    if (id && userId) void load()
  },
  { immediate: true },
)
</script>

<template>
  <main class="conversations-page">
    <header class="page-toolbar workspace-card conversations-heading">
      <div>
        <el-button link @click="router.push(`/knowledge-bases/${knowledgeBaseId}/documents`)">
          ‹ 文档
        </el-button>
        <h2>{{ activeKnowledgeBase?.name ?? '知识库问答' }}</h2>
        <p>会话和回答保存在服务端，切换设备后仍可继续查看。</p>
      </div>
      <el-button
        data-test="new-conversation"
        type="primary"
        :loading="conversations.creating"
        :disabled="structuralOperation || conversations.isStreaming"
        @click="startNewConversation"
      >
        新建会话
      </el-button>
    </header>

    <el-alert
      v-if="pageError"
      data-test="conversation-page-error"
      type="error"
      :title="pageError"
      show-icon
      :closable="false"
    >
      <el-button link type="primary" @click="load(true)">重新加载</el-button>
    </el-alert>

    <div class="conversations-layout">
      <ConversationList
        :conversations="visibleConversations"
        :active-conversation-id="conversations.activeConversationId"
        :loading="conversations.loading"
        :switching-id="switchingId"
        :deleting-id="deletingId"
        :clearing="conversations.clearing"
        :actions-disabled="structuralOperation || conversations.submitting || conversations.isStreaming"
        :selection-disabled="structuralOperation"
        :has-more="visibleConversations.length < conversations.total"
        @open="openConversation"
        @delete="deleteConversation"
        @clear="clearHistory"
        @load-more="loadMoreConversations"
      />

      <section v-if="conversationContextReady" class="workspace-card conversation-workspace" aria-label="当前会话">
        <QuestionPanel :show-history-actions="false" />
      </section>
      <section v-else class="workspace-card conversation-workspace" aria-label="当前会话加载状态">
        <el-skeleton :rows="5" animated />
      </section>
    </div>
  </main>
</template>

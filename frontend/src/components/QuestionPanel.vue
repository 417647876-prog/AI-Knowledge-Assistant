<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { formatApiError } from '../api/client'
import { useAuthStore } from '../stores/auth'
import { useConversationsStore } from '../stores/conversations'
import { useWorkspaceStore } from '../stores/workspace'
import ConversationTimeline from './ConversationTimeline.vue'

withDefaults(defineProps<{ showHistoryActions?: boolean }>(), { showHistoryActions: true })

const auth = useAuthStore()
const workspace = useWorkspaceStore()
const conversations = useConversationsStore()
const question = ref('')

async function submitOrStop() {
  if (conversations.isStreaming) {
    conversations.stop()
    return
  }
  if (!auth.user?.id || !workspace.activeKnowledgeBaseId) return
  const value = question.value.trim()
  if (!value) return ElMessage.warning('请输入问题。')

  question.value = ''
  try {
    await conversations.submit(value)
  } catch (error) {
    ElMessage.error(formatApiError(error))
  }
}

async function clearHistory() {
  try {
    await ElMessageBox.confirm(
      '确定清空当前知识库的全部问答历史吗？',
      '清空历史',
      { type: 'warning' },
    )
    await conversations.clear()
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(formatApiError(error))
  }
}

async function startNewConversation() {
  try {
    await conversations.newConversation()
  } catch (error) {
    ElMessage.error(formatApiError(error))
  }
}

async function retryAnswer(answerId: string): Promise<void> {
  try {
    await conversations.retry(answerId)
  } catch (error) {
    ElMessage.error(formatApiError(error))
  }
}
</script>

<template>
  <section class="question-panel">
    <div v-if="showHistoryActions" class="question-toolbar">
      <el-button
        data-test="new-conversation"
        :loading="conversations.creating"
        :disabled="conversations.creating || conversations.clearing || (conversations.submitting && !conversations.isStreaming)"
        @click="startNewConversation"
      >
        新建会话
      </el-button>
      <el-button
        data-test="clear-conversation"
        :loading="conversations.clearing"
        :disabled="conversations.creating || conversations.submitting || conversations.isStreaming || conversations.clearing"
        @click="clearHistory"
      >
        清空历史
      </el-button>
    </div>
    <ConversationTimeline :messages="conversations.messages" @retry="retryAnswer" />
    <div class="question-composer">
      <label for="knowledge-question">向当前知识库提问</label>
      <el-input
        v-model="question"
        input-id="knowledge-question"
        type="textarea"
        maxlength="2000"
        show-word-limit
        placeholder="例如：这份制度的适用范围是什么？"
        :autosize="{ minRows: 2, maxRows: 6 }"
        :disabled="conversations.clearing"
        @keydown.ctrl.enter.prevent="submitOrStop"
        @keydown.meta.enter.prevent="submitOrStop"
      />
      <div class="question-actions">
        <span class="question-shortcut">Ctrl + Enter 提交</span>
        <el-button
          data-test="submit-question"
          :type="conversations.isStreaming ? 'danger' : 'primary'"
          :loading="conversations.submitting && !conversations.isStreaming"
          :disabled="!auth.user?.id || !workspace.activeKnowledgeBaseId || (conversations.submitting && !conversations.isStreaming) || conversations.clearing"
          @click="submitOrStop"
        >
          {{ conversations.isStreaming ? '停止生成' : '发送问题' }}
        </el-button>
      </div>
    </div>
  </section>
</template>

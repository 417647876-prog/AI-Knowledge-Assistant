<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { formatApiError } from '../api/client'
import { useAuthStore } from '../stores/auth'
import { useConversationsStore } from '../stores/conversations'
import { useWorkspaceStore } from '../stores/workspace'
import ConversationTimeline from './ConversationTimeline.vue'

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
    conversations.clear()
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(formatApiError(error))
  }
}
</script>

<template>
  <section class="question-panel">
    <div class="question-toolbar">
      <el-button data-test="new-conversation" @click="conversations.newConversation">
        新建会话
      </el-button>
      <el-button data-test="clear-conversation" @click="clearHistory">清空历史</el-button>
    </div>
    <ConversationTimeline :messages="conversations.messages" @retry="conversations.retry" />
    <el-input
      v-model="question"
      type="textarea"
      maxlength="2000"
      placeholder="请输入关于当前知识库的问题"
      :autosize="{ minRows: 3, maxRows: 8 }"
      @keydown.ctrl.enter.prevent="submitOrStop"
    />
    <div class="question-actions">
      <el-button
        data-test="submit-question"
        type="primary"
        :disabled="!auth.user?.id || !workspace.activeKnowledgeBaseId"
        @click="submitOrStop"
      >
        {{ conversations.isStreaming ? '停止' : '提问' }}
      </el-button>
    </div>
  </section>
</template>

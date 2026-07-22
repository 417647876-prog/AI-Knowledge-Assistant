<script setup lang="ts">
import type { ConversationSummary } from '../types/api'

defineProps<{
  conversations: ConversationSummary[]
  activeConversationId: string | null
  loading: boolean
  switchingId: string | null
  deletingId: string | null
  clearing: boolean
  actionsDisabled: boolean
  selectionDisabled: boolean
  hasMore: boolean
}>()
const emit = defineEmits<{
  open: [conversationId: string]
  delete: [conversationId: string, title: string]
  clear: []
  loadMore: []
}>()

function formatUpdatedAt(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(new Date(value))
}
</script>

<template>
  <aside class="workspace-card conversation-history-card" aria-label="会话历史">
    <header class="conversation-history-heading">
      <h3>会话记录</h3>
      <el-button
        data-test="clear-conversation"
        text
        :loading="clearing"
        :disabled="actionsDisabled"
        @click="emit('clear')"
      >
        清空
      </el-button>
    </header>

    <el-skeleton v-if="loading && !conversations.length" :rows="3" animated />
    <div
      v-else-if="conversations.length"
      data-test="conversation-history"
      class="conversation-history-list"
    >
      <article
        v-for="conversation in conversations"
        :key="conversation.id"
        class="conversation-history-item"
        :class="{ active: conversation.id === activeConversationId }"
      >
        <button
          type="button"
          class="conversation-history-select"
          :data-test="`conversation-item-${conversation.id}`"
          :aria-current="conversation.id === activeConversationId ? 'true' : undefined"
          :disabled="selectionDisabled || Boolean(switchingId)"
          @click="emit('open', conversation.id)"
        >
          <strong>{{ conversation.title }}</strong>
          <span>{{ formatUpdatedAt(conversation.updated_at) }}</span>
        </button>
        <el-button
          text
          class="conversation-history-delete"
          :loading="deletingId === conversation.id"
          :disabled="actionsDisabled || Boolean(deletingId)"
          :aria-label="`删除会话 ${conversation.title}`"
          @click="emit('delete', conversation.id, conversation.title)"
        >
          删除
        </el-button>
      </article>
      <el-button
        v-if="hasMore"
        data-test="load-more-conversations"
        class="conversation-history-more"
        :loading="loading"
        :disabled="actionsDisabled || selectionDisabled"
        @click="emit('loadMore')"
      >
        加载更多
      </el-button>
    </div>
    <p v-else class="conversation-history-empty">暂无会话。提出问题后会自动创建。</p>
  </aside>
</template>

<script setup lang="ts">
import type { AssistantMessage, ConversationMessage as Message } from '../types/conversation'
import ConversationMessage from './ConversationMessage.vue'
import RetrievalDetails from './RetrievalDetails.vue'

const props = defineProps<{ messages: Message[] }>()
const emit = defineEmits<{ retry: [answerId: string] }>()

function answerFor(questionId: string): AssistantMessage | null {
  const item = props.messages.find(
    (candidate) => candidate.kind === 'assistant' && candidate.questionId === questionId,
  )
  return item?.kind === 'assistant' ? item : null
}
</script>

<template>
  <section v-if="messages.length" class="conversation-timeline" aria-live="polite">
    <template v-for="message in messages" :key="message.id">
      <el-divider v-if="message.kind === 'divider'" class="conversation-divider">新会话</el-divider>
      <ConversationMessage v-else :message="message" @retry="emit('retry', $event)" />
      <RetrievalDetails
        v-if="message.kind === 'user' && answerFor(message.id)"
        class="question-retrieval"
        :message="answerFor(message.id)!"
      />
    </template>
  </section>
</template>

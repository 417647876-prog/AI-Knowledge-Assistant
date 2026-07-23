<script setup lang="ts">
import { ref } from 'vue'
import type { Citation } from '../types/api'
import type { AssistantMessage, ConversationMessage as Message } from '../types/conversation'
import CitationDrawer from './CitationDrawer.vue'
import ConversationMessage from './ConversationMessage.vue'
import RetrievalDetails from './RetrievalDetails.vue'

const props = defineProps<{ messages: Message[] }>()
const emit = defineEmits<{ retry: [answerId: string] }>()
const citationDrawerVisible = ref(false)
const selectedCitations = ref<Citation[]>([])

function openCitations(citations: Citation[]): void {
  selectedCitations.value = [...citations]
  citationDrawerVisible.value = true
}

function answerFor(questionId: string): AssistantMessage | null {
  const item = [...props.messages].reverse().find(
    (candidate) => candidate.kind === 'assistant' && candidate.questionId === questionId,
  )
  return item?.kind === 'assistant' ? item : null
}
</script>

<template>
  <section v-if="messages.length" class="conversation-timeline" aria-live="polite">
    <template v-for="message in messages" :key="message.id">
      <el-divider v-if="message.kind === 'divider'" class="conversation-divider">新会话</el-divider>
      <div v-else class="conversation-timeline-entry">
        <ConversationMessage
          :message="message"
          :show-citations="false"
          @retry="emit('retry', $event)"
        />
        <el-button
          v-if="message.kind === 'assistant' && message.citations.length"
          :data-test="`open-citations-${message.id}`"
          class="open-citations"
          type="primary"
          link
          @click="openCitations(message.citations)"
        >
          查看引用（{{ message.citations.length }}）
        </el-button>
      </div>
      <RetrievalDetails
        v-if="message.kind === 'user' && answerFor(message.id)"
        class="question-retrieval"
        :message="answerFor(message.id)!"
      />
    </template>
    <CitationDrawer v-model="citationDrawerVisible" :citations="selectedCitations" />
  </section>
</template>

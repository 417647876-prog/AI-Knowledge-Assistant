<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { ConversationMessage as Message } from '../types/conversation'
import { renderSafeMarkdown } from '../utils/markdown'
import CitationList from './CitationList.vue'

const props = withDefaults(defineProps<{
  message: Message
  showCitations?: boolean
}>(), { showCitations: true })
const emit = defineEmits<{ retry: [answerId: string] }>()

const rendered = ref('')
const assistant = computed(() => props.message.kind === 'assistant' ? props.message : null)
const retryable = computed(() => assistant.value
  && ['failed', 'interrupted', 'stopped'].includes(assistant.value.status))
const phaseText = computed(() => {
  const phase = assistant.value?.phase
  if (phase === 'rewriting') return '正在改写问题'
  if (phase === 'retrieving') return '正在检索资料'
  if (phase === 'generating') return '正在生成回答'
  return '正在处理问题'
})
let timer: ReturnType<typeof setTimeout> | null = null

function render(content: string) {
  rendered.value = renderSafeMarkdown(content)
}

watch(
  () => props.message.kind === 'assistant' ? props.message.content : '',
  () => {
    if (timer) return
    timer = setTimeout(() => {
      render(props.message.kind === 'assistant' ? props.message.content : '')
      timer = null
    }, 50)
  },
  { immediate: true },
)

watch(
  () => props.message.kind === 'assistant' ? props.message.status : null,
  (status) => {
    if (status && status !== 'streaming' && props.message.kind === 'assistant') {
      if (timer) clearTimeout(timer)
      timer = null
      render(props.message.content)
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  if (timer) clearTimeout(timer)
})
</script>

<template>
  <article :class="['conversation-message', message.kind]">
    <p v-if="message.kind === 'user'" class="user-content">{{ message.content }}</p>
    <template v-else-if="assistant">
      <p v-if="assistant.status === 'streaming'" class="message-status">{{ phaseText }}</p>
      <div v-if="rendered" class="markdown-body" v-html="rendered" />
      <el-alert v-if="assistant.status === 'failed'" type="error" :closable="false">
        <template #title>回答失败 [{{ assistant.errorCode ?? 'STREAM_ERROR' }}]</template>
        <p v-if="assistant.requestId">请求标识：{{ assistant.requestId }}</p>
      </el-alert>
      <el-alert
        v-else-if="assistant.status === 'interrupted'"
        type="warning"
        title="回答已中断"
        description="已保留收到的内容，可手动重新生成。"
        :closable="false"
      />
      <el-alert
        v-else-if="assistant.status === 'stopped'"
        type="info"
        title="回答已停止"
        description="需要时可以手动重新生成。"
        :closable="false"
      />
      <el-button
        v-if="retryable"
        data-test="retry-answer"
        class="regenerate-answer"
        type="primary"
        plain
        @click="emit('retry', assistant.id)"
      >
        重新生成
      </el-button>
      <CitationList v-if="showCitations" :citations="assistant.citations" />
    </template>
  </article>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { formatApiError } from '../api/client'
import { useWorkspaceStore } from '../stores/workspace'

const store = useWorkspaceStore()
const question = ref('')

async function submit() {
  if (!question.value.trim()) return ElMessage.warning('请输入问题。')
  try {
    await store.submitQuestion(question.value)
  } catch (error) {
    ElMessage.error(formatApiError(error))
  }
}
</script>

<template>
  <section class="question-panel">
    <el-input
      v-model="question"
      type="textarea"
      maxlength="2000"
      placeholder="请输入关于当前知识库的问题"
      :autosize="{ minRows: 3, maxRows: 8 }"
    />
    <el-button
      data-test="submit-question"
      type="primary"
      :disabled="!store.activeKnowledgeBaseId"
      :loading="store.asking"
      @click="submit"
    >
      提问
    </el-button>

    <article v-if="store.answer" class="answer">
      <h3>回答</h3>
      <p>{{ store.answer.answer }}</p>
      <div class="answer-meta">
        <span>检索片段数：{{ store.answer.retrieved_chunk_count }}</span>
        <span>请求标识：{{ store.answer.request_id }}</span>
      </div>

      <section v-if="store.answer.citations.length" data-test="citations" class="citations">
        <h3>引用</h3>
        <el-card
          v-for="citation in store.answer.citations"
          :key="citation.citation_id"
          class="citation-card"
        >
          <template #header>
            <strong>{{ citation.file_name }}</strong>
          </template>
          <p>{{ citation.content }}</p>
          <div class="citation-meta">
            <span>相关度 {{ citation.relevance_score.toFixed(2) }}</span>
            <span v-if="citation.page_number !== null">第 {{ citation.page_number }} 页</span>
            <span v-if="citation.sheet_name !== null">工作表：{{ citation.sheet_name }}</span>
            <span v-if="citation.row_start !== null">行号：{{ citation.row_start }}</span>
            <span v-if="citation.section_title !== null">章节：{{ citation.section_title }}</span>
          </div>
        </el-card>
      </section>
    </article>
  </section>
</template>

<style scoped>
.question-panel { display: grid; gap: 16px; }
.question-panel > .el-button { justify-self: end; }
.answer, .citations { display: grid; gap: 12px; }
.answer h3, .answer p, .citations h3 { margin: 0; }
.answer-meta, .citation-meta { display: flex; flex-wrap: wrap; gap: 8px 16px; color: #606266; }
</style>

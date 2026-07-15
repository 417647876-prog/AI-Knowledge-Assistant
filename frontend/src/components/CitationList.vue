<script setup lang="ts">
import type { Citation } from '../types/api'

defineProps<{ citations: Citation[] }>()
</script>

<template>
  <section v-if="citations.length" data-test="citations" class="citation-list">
    <h4>引用</h4>
    <el-card v-for="citation in citations" :key="citation.citation_id" class="citation-card">
      <template #header>
        <strong>[{{ citation.citation_id }}] {{ citation.file_name }}</strong>
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
</template>

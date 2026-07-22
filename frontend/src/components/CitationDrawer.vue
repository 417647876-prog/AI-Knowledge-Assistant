<script setup lang="ts">
import { computed } from 'vue'
import type { Citation } from '../types/api'

const props = defineProps<{
  modelValue: boolean
  citations: Citation[]
}>()
const emit = defineEmits<{ 'update:modelValue': [visible: boolean] }>()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})
</script>

<template>
  <el-drawer
    v-model="visible"
    direction="btt"
    size="min(78dvh, 680px)"
    :with-header="false"
    append-to-body
    class="citation-drawer"
  >
    <section data-test="citation-drawer" class="citation-drawer-content" aria-labelledby="citation-drawer-title">
      <header class="citation-drawer-header">
        <div>
          <h3 id="citation-drawer-title">引用来源</h3>
          <p>以下内容是回答生成时保存的引用快照。</p>
        </div>
        <el-button data-test="close-citation-drawer" aria-label="关闭引用来源" @click="visible = false">
          关闭
        </el-button>
      </header>

      <div class="citation-snapshot-list">
        <article
          v-for="citation in citations"
          :key="citation.citation_id"
          class="citation-snapshot"
        >
          <header>
            <strong>[{{ citation.citation_id }}] {{ citation.file_name }}</strong>
            <span v-if="citation.relevance_score !== null">相关度 {{ citation.relevance_score.toFixed(2) }}</span>
          </header>
          <p class="citation-snapshot-content">{{ citation.content }}</p>
          <p class="citation-snapshot-meta">
            <span v-if="citation.page_number !== null">第 {{ citation.page_number }} 页</span>
            <span v-if="citation.sheet_name !== null">工作表：{{ citation.sheet_name }}</span>
            <span v-if="citation.row_start !== null">行号：{{ citation.row_start }}</span>
            <span v-if="citation.section_title !== null">章节：{{ citation.section_title }}</span>
          </p>
        </article>
      </div>
    </section>
  </el-drawer>
</template>

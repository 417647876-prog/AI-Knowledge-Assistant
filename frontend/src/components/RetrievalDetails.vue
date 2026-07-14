<script setup lang="ts">
import type { AssistantMessage } from '../types/conversation'

defineProps<{ message: AssistantMessage }>()
</script>

<template>
  <el-collapse data-test="retrieval-details">
    <el-collapse-item title="检索详情" name="details">
      <dl>
        <dt>改写后的问题</dt>
        <dd>{{ message.standaloneQuestion ?? '处理中' }}</dd>
        <dt>检索片段数</dt>
        <dd>{{ message.retrievedChunkCount ?? '处理中' }}</dd>
        <dt>问题改写耗时</dt>
        <dd>{{ message.timings?.rewrite_ms ?? 0 }} ms</dd>
        <dt>检索耗时</dt>
        <dd>{{ message.timings?.retrieval_ms ?? 0 }} ms</dd>
        <dt>回答生成耗时</dt>
        <dd>{{ message.timings?.generation_ms ?? 0 }} ms</dd>
        <dt>总耗时</dt>
        <dd>{{ message.timings?.total_ms ?? 0 }} ms</dd>
      </dl>
    </el-collapse-item>
  </el-collapse>
</template>

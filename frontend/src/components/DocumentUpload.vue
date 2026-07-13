<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { formatApiError } from '../api/client'
import { useWorkspaceStore } from '../stores/workspace'

const store = useWorkspaceStore()
const uploading = ref(false)

async function upload(event: Event) {
  if (uploading.value) return
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return

  uploading.value = true
  try {
    await store.uploadAndTrackDocument(file)
    ElMessage.success('文档上传并处理完成。')
  } catch (error) {
    ElMessage.error(formatApiError(error))
  } finally {
    uploading.value = false
    input.value = ''
  }
}
</script>

<template>
  <section class="document-upload">
    <label for="document-file">上传文档</label>
    <input
      id="document-file"
      type="file"
      accept=".txt,.md,.pdf,.docx,.xlsx"
      :disabled="uploading"
      @change="upload"
    >
    <span v-if="uploading">上传处理中…</span>
  </section>
</template>

<style scoped>
.document-upload {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  max-width: 100%;
  gap: 12px;
  margin-bottom: 16px;
}
.document-upload input { min-width: 0; max-width: 100%; }
</style>

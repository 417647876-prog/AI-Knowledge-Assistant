<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { formatApiError } from '../api/client'
import { useWorkspaceStore } from '../stores/workspace'
import type { DocumentTask } from '../types/api'

const store = useWorkspaceStore()
const labels = {
  pending: '等待处理', parsing: '解析中', embedding: '向量化中', ready: '可用', failed: '处理失败',
} as const

const isProcessing = (status: DocumentTask['status']) =>
  status === 'pending' || status === 'parsing' || status === 'embedding'
const activeActions = ref<Record<string, 'reprocess' | 'delete'>>({})

function startAction(documentId: string, action: 'reprocess' | 'delete') {
  if (activeActions.value[documentId]) return false
  activeActions.value = { ...activeActions.value, [documentId]: action }
  return true
}

function finishAction(documentId: string) {
  const remaining = { ...activeActions.value }
  delete remaining[documentId]
  activeActions.value = remaining
}

async function reprocess(document: DocumentTask) {
  if (!startAction(document.document_id, 'reprocess')) return
  try {
    await store.reprocessDocument(document.document_id)
    ElMessage.success('已提交重新处理任务。')
  } catch (error) {
    ElMessage.error(formatApiError(error))
  } finally {
    finishAction(document.document_id)
  }
}

async function remove(document: DocumentTask) {
  if (!startAction(document.document_id, 'delete')) return
  try {
    await ElMessageBox.confirm(`确定删除“${document.file_name}”吗？可在回收站恢复。`, '删除文档', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
    await store.deleteDocument(document.document_id)
    ElMessage.success('文档已删除。')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close')
      ElMessage.error(formatApiError(error))
  } finally {
    finishAction(document.document_id)
  }
}
</script>

<template>
  <div class="document-table-desktop">
    <el-table :data="store.activeDocuments" empty-text="当前知识库暂无文档">
      <el-table-column prop="file_name" label="文件名" />
      <el-table-column label="状态"><template #default="scope">{{ labels[scope.row.status as keyof typeof labels] }}</template></el-table-column>
      <el-table-column prop="error_code" label="错误代码" />
      <el-table-column prop="error_message" label="错误信息" />
      <el-table-column label="操作" width="180"><template #default="scope">
        <el-button :data-test="`reprocess-${scope.row.document_id}`" link type="primary" :loading="activeActions[scope.row.document_id] === 'reprocess'" :disabled="isProcessing(scope.row.status) || Boolean(activeActions[scope.row.document_id])" @click="reprocess(scope.row)">重新处理</el-button>
        <el-button :data-test="`delete-${scope.row.document_id}`" link type="danger" :loading="activeActions[scope.row.document_id] === 'delete'" :disabled="isProcessing(scope.row.status) || Boolean(activeActions[scope.row.document_id])" @click="remove(scope.row)">删除</el-button>
      </template></el-table-column>
    </el-table>
  </div>
  <section class="document-list-mobile" aria-label="文档列表">
    <p v-if="!store.activeDocuments.length">当前知识库暂无文档</p>
    <article v-for="document in store.activeDocuments" :key="document.document_id" class="document-card">
      <strong>{{ document.file_name }}</strong>
      <span>状态：{{ labels[document.status] }}</span>
      <span v-if="document.error_code">错误：{{ document.error_code }}</span>
      <span v-if="document.error_message">说明：{{ document.error_message }}</span>
      <div class="document-card-actions">
        <el-button :data-test="`reprocess-mobile-${document.document_id}`" :disabled="isProcessing(document.status) || Boolean(activeActions[document.document_id])" :loading="activeActions[document.document_id] === 'reprocess'" @click="reprocess(document)">重新处理</el-button>
        <el-button :data-test="`delete-mobile-${document.document_id}`" type="danger" :disabled="isProcessing(document.status) || Boolean(activeActions[document.document_id])" :loading="activeActions[document.document_id] === 'delete'" @click="remove(document)">删除</el-button>
      </div>
    </article>
  </section>
</template>

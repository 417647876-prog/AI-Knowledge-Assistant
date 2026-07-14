<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { useWorkspaceStore } from '../stores/workspace'
import type { DocumentTask } from '../types/api'

const store = useWorkspaceStore()
const labels = {
  pending: '等待处理', parsing: '解析中', embedding: '向量化中', ready: '可用', failed: '处理失败',
} as const

const isProcessing = (status: DocumentTask['status']) =>
  status === 'pending' || status === 'parsing' || status === 'embedding'

async function reprocess(document: DocumentTask) {
  try {
    await store.reprocessDocument(document.document_id)
    ElMessage.success('已提交重新处理任务。')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '重新处理失败。')
  }
}

async function remove(document: DocumentTask) {
  try {
    await ElMessageBox.confirm(`确定删除“${document.file_name}”吗？删除后无法恢复。`, '删除文档', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
    await store.deleteDocument(document.document_id)
    ElMessage.success('文档已删除。')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close')
      ElMessage.error(error instanceof Error ? error.message : '删除文档失败。')
  }
}
</script>

<template>
  <el-table :data="store.activeDocuments" empty-text="当前会话还没有上传文档">
    <el-table-column prop="file_name" label="文件名" />
    <el-table-column label="状态">
      <template #default="scope">
        {{ labels[scope.row.status as keyof typeof labels] }}
      </template>
    </el-table-column>
    <el-table-column prop="error_message" label="错误信息" />
    <el-table-column label="操作" width="180">
      <template #default="scope">
        <el-button
          v-if="scope.row.status === 'failed'"
          :data-test="`reprocess-${scope.row.document_id}`"
          link
          type="primary"
          @click="reprocess(scope.row)"
        >
          重新处理
        </el-button>
        <el-button
          :data-test="`delete-${scope.row.document_id}`"
          link
          type="danger"
          :disabled="isProcessing(scope.row.status)"
          @click="remove(scope.row)"
        >
          删除
        </el-button>
      </template>
    </el-table-column>
  </el-table>
</template>

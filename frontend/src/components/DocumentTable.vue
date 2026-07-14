<script setup lang="ts">
import { useWorkspaceStore } from '../stores/workspace'

const store = useWorkspaceStore()
const labels = {
  pending: '等待处理', running: '处理中', ready: '可用', failed: '处理失败',
} as const
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
  </el-table>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { formatApiError } from '../api/client'
import { useWorkspaceStore } from '../stores/workspace'

const store = useWorkspaceStore()
const error = ref<string | null>(null)
const pending = ref<string | null>(null)

async function load(): Promise<void> { error.value = null; try { await store.loadTrash() } catch (reason) { error.value = formatApiError(reason) } }
async function restore(type: 'knowledge-base' | 'document', id: string): Promise<void> {
  pending.value = id
  try { if (type === 'knowledge-base') await store.restoreTrashKnowledgeBaseItem(id); else await store.restoreTrashDocumentItem(id); ElMessage.success('已恢复。') }
  catch (reason) { ElMessage.error(formatApiError(reason)) } finally { pending.value = null }
}
async function purge(type: 'knowledge-base' | 'document', id: string, name: string): Promise<void> {
  try {
    await ElMessageBox.confirm(`永久清理“${name}”会创建异步任务且不可撤销，确认继续吗？`, '永久清理确认', { type: 'warning' })
    pending.value = id
    const job = type === 'knowledge-base' ? await store.purgeTrashKnowledgeBaseItem(id) : await store.purgeTrashDocumentItem(id)
    ElMessage.success(`已创建永久清理任务：${job.status}`)
  } catch (reason) { if (reason !== 'cancel' && reason !== 'close') ElMessage.error(formatApiError(reason)) }
  finally { pending.value = null }
}
onMounted(() => { void load() })
</script>

<template>
  <main class="trash-page"><section class="workspace-card"><header class="page-toolbar"><div><router-link to="/">‹ 知识库</router-link><h2>回收站</h2></div><el-button :loading="store.loadingTrash" @click="load">刷新</el-button></header><p v-if="error" class="admin-users-error">{{ error }}</p><section class="trash-section"><h3>知识库</h3><article v-for="item in store.trash.knowledge_bases" :key="item.id" class="trash-card"><strong>{{ item.name }}</strong><span>自动清理：{{ new Date(item.purge_after).toLocaleString('zh-CN') }}</span><div><el-button :loading="pending === item.id" @click="restore('knowledge-base', item.id)">恢复</el-button><el-button :loading="pending === item.id" type="danger" @click="purge('knowledge-base', item.id, item.name)">永久清理</el-button></div></article><p v-if="!store.trash.knowledge_bases.length">暂无已删除知识库。</p></section><section class="trash-section"><h3>文档</h3><article v-for="item in store.trash.documents" :key="item.id" class="trash-card"><strong>{{ item.file_name }}</strong><span>自动清理：{{ new Date(item.purge_after).toLocaleString('zh-CN') }}</span><div><el-button :loading="pending === item.id" @click="restore('document', item.id)">恢复</el-button><el-button :loading="pending === item.id" type="danger" @click="purge('document', item.id, item.file_name)">永久清理</el-button></div></article><p v-if="!store.trash.documents.length">暂无已删除文档。</p></section></section></main>
</template>

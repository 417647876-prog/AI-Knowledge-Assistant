<script setup lang="ts">
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { formatApiError } from '../api/client'
import { useAuthStore } from '../stores/auth'
import { useWorkspaceStore } from '../stores/workspace'

const auth = useAuthStore()
const store = useWorkspaceStore()
const dialogVisible = ref(false)
const submitting = ref(false)
const form = reactive({ name: '', description: '' })

async function submit() {
  if (!form.name.trim()) return ElMessage.warning('请输入知识库名称。')
  submitting.value = true
  try {
    await store.createKnowledgeBase({
      name: form.name.trim(), description: form.description.trim() || null,
    })
    dialogVisible.value = false
    form.name = ''; form.description = ''
  } catch (error) {
    ElMessage.error(formatApiError(error))
  } finally { submitting.value = false }
}
</script>

<template>
  <aside class="knowledge-base-sidebar">
    <div class="sidebar-heading">
      <h2>知识库</h2>
      <el-button data-test="create-knowledge-base" type="primary" @click="dialogVisible = true">
        新建
      </el-button>
    </div>

    <el-menu
      :default-active="store.activeKnowledgeBaseId ?? undefined"
      @select="store.selectKnowledgeBase"
    >
      <el-menu-item v-for="item in store.knowledgeBases" :key="item.id" :index="item.id">
        <span class="knowledge-base-label">
          <span class="knowledge-base-name" :title="item.name">{{ item.name }}</span>
          <small
            v-if="auth.isAdmin"
            data-test="knowledge-base-owner"
            class="knowledge-base-owner"
          >
            所有者：{{ item.owner_username }}
          </small>
        </span>
      </el-menu-item>
    </el-menu>

    <el-dialog
      v-model="dialogVisible"
      data-test="knowledge-base-dialog"
      title="新建知识库"
      width="480px"
    >
      <form @submit.prevent="submit">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="请输入知识库名称" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" placeholder="可选" />
        </el-form-item>
        <div class="dialog-actions">
          <el-button native-type="button" @click="dialogVisible = false">取消</el-button>
          <el-button native-type="submit" type="primary" :loading="submitting">创建</el-button>
        </div>
      </form>
    </el-dialog>
  </aside>
</template>

<style scoped>
.knowledge-base-sidebar, .el-menu, .el-menu-item {
  min-width: 0;
  max-width: 100%;
}
.knowledge-base-name {
  display: block;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.knowledge-base-label { display: grid; min-width: 0; line-height: 1.4; }
.knowledge-base-owner { color: #667085; }
.sidebar-heading { display: flex; align-items: center; justify-content: space-between; padding: 16px; }
.sidebar-heading h2 { margin: 0; font-size: 18px; }
.dialog-actions { display: flex; justify-content: flex-end; gap: 8px; }
</style>

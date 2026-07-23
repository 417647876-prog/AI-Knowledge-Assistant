<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { formatApiError } from '../api/client'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const loggingOut = ref(false)

async function logout() {
  loggingOut.value = true
  try {
    await auth.logout()
  } catch (error) {
    ElMessage.error(formatApiError(error))
  } finally {
    loggingOut.value = false
    await router.replace('/login')
  }
}
</script>

<template>
  <header class="app-header desktop-header">
    <h1>AI 知识库助手</h1>
    <div v-if="auth.user" class="header-user">
      <span>{{ auth.user.username }}</span>
      <span>{{ auth.isAdmin ? '管理员' : '普通用户' }}</span>
      <nav class="desktop-navigation" aria-label="桌面主导航">
        <router-link data-test="workspace-link" to="/">工作台</router-link>
        <router-link data-test="profile-link" to="/profile">我的</router-link>
        <router-link v-if="auth.isAdmin" data-test="admin-users-link" to="/admin/users">
          用户管理
        </router-link>
        <router-link v-if="auth.isAdmin" data-test="admin-operations-link" to="/admin/operations">
          运营概览
        </router-link>
      </nav>
      <el-button data-test="logout" :loading="loggingOut" @click="logout">
        退出
      </el-button>
    </div>
  </header>
</template>

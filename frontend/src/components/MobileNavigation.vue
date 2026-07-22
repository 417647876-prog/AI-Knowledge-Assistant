<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { formatApiError } from '../api/client'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const loggingOut = ref(false)

function isCurrent(path: string): boolean {
  return route.path === path
}

async function logout(): Promise<void> {
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
  <nav class="mobile-navigation" aria-label="手机主导航" data-test="mobile-navigation">
    <router-link
      to="/"
      data-test="mobile-workspace-link"
      class="mobile-navigation-link"
      :aria-current="isCurrent('/') ? 'page' : undefined"
    >
      工作台
    </router-link>
    <router-link to="/trash" class="mobile-navigation-link" :aria-current="isCurrent('/trash') ? 'page' : undefined">回收站</router-link>
    <router-link
      to="/profile"
      data-test="mobile-profile-link"
      class="mobile-navigation-link"
      :aria-current="isCurrent('/profile') ? 'page' : undefined"
    >
      我的
    </router-link>
    <router-link
      v-if="auth.isAdmin"
      to="/admin/users"
      data-test="mobile-admin-users-link"
      class="mobile-navigation-link"
      :aria-current="isCurrent('/admin/users') ? 'page' : undefined"
    >
      用户管理
    </router-link>
    <router-link
      v-if="auth.isAdmin"
      to="/admin/operations"
      data-test="mobile-admin-operations-link"
      class="mobile-navigation-link"
      :aria-current="isCurrent('/admin/operations') ? 'page' : undefined"
    >
      运营
    </router-link>
    <el-button
      data-test="mobile-logout"
      text
      :loading="loggingOut"
      aria-label="退出登录"
      @click="logout"
    >
      退出
    </el-button>
  </nav>
</template>

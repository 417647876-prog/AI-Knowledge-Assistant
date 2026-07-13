<script setup lang="ts">
import { RouterView } from 'vue-router'
import AppHeader from './components/AppHeader.vue'
import { useAuthStore } from './stores/auth'

const auth = useAuthStore()
</script>

<template>
  <div class="app-shell">
    <div v-if="auth.initializing" data-test="auth-loading" class="auth-loading">
      正在恢复登录状态…
    </div>
    <template v-else>
      <AppHeader v-if="auth.user" />
      <RouterView />
    </template>
  </div>
</template>

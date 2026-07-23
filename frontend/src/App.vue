<script setup lang="ts">
import { watch } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import AppHeader from './components/AppHeader.vue'
import MobileNavigation from './components/MobileNavigation.vue'
import { useAuthStore } from './stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

watch(() => auth.user, (user) => {
  if (
    !user
    && auth.initialized
    && !auth.initializing
    && route.meta.requiresAuth
  ) void router.replace('/login')
})
</script>

<template>
  <div class="app-shell" :class="{ 'app-shell--authenticated': Boolean(auth.user) }">
    <div v-if="auth.initializing" data-test="auth-loading" class="auth-loading">
      正在恢复登录状态…
    </div>
    <template v-else>
      <AppHeader v-if="auth.user" />
      <div class="app-content">
        <RouterView />
      </div>
      <MobileNavigation v-if="auth.user" />
    </template>
  </div>
</template>

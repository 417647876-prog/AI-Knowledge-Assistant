<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { formatApiError } from '../api/client'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const submitting = ref(false)
const loginError = ref<string | null>(null)
const form = reactive({ username: '', password: '' })

async function submit() {
  if (submitting.value) return
  loginError.value = null
  submitting.value = true
  try {
    await auth.login(form.username, form.password)
    await router.replace('/')
  } catch (error) {
    loginError.value = formatApiError(error)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-card workspace-card">
      <h1>登录 AI 知识库助手</h1>
      <form @submit.prevent="submit">
        <p v-if="loginError" data-test="login-error" class="login-error" role="alert">
          {{ loginError }}
        </p>
        <el-form-item label="用户名">
          <el-input
            v-model="form.username"
            data-test="login-username"
            name="username"
            autocomplete="username"
            autocapitalize="none"
            autocorrect="off"
            spellcheck="false"
            inputmode="text"
            enterkeyhint="next"
          />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="form.password"
            data-test="login-password"
            name="password"
            type="password"
            autocomplete="current-password"
            autocapitalize="none"
            autocorrect="off"
            spellcheck="false"
            inputmode="text"
            enterkeyhint="go"
          />
        </el-form-item>
        <el-button native-type="submit" type="primary" :loading="submitting">
          登录
        </el-button>
      </form>
    </section>
  </main>
</template>

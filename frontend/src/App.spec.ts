import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createMemoryHistory } from 'vue-router'
import { describe, expect, it } from 'vitest'
import App from './App.vue'
import AppHeader from './components/AppHeader.vue'
import { createAppRouter } from './router'
import { useAuthStore } from './stores/auth'
import LoginView from './views/LoginView.vue'

describe('App', () => {
  it('登录路由显示登录页且不显示用户头部', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.initialized = true
    const router = createAppRouter(createMemoryHistory())
    await router.push('/login')
    await router.isReady()

    const wrapper = mount(App, {
      global: { plugins: [pinia, router, ElementPlus] },
    })

    expect(wrapper.findComponent(LoginView).exists()).toBe(true)
    expect(wrapper.findComponent(AppHeader).exists()).toBe(false)
  })

  it('认证会话初始化期间显示加载提示', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.initialized = true
    const router = createAppRouter(createMemoryHistory())
    await router.push('/login')
    await router.isReady()
    auth.initialized = false
    auth.initializing = true

    const wrapper = mount(App, {
      global: { plugins: [pinia, router, ElementPlus] },
    })

    expect(wrapper.get('[data-test="auth-loading"]').text()).toContain('正在恢复登录状态')
    expect(wrapper.findComponent(LoginView).exists()).toBe(false)
  })
})

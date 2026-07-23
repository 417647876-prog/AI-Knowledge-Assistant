import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createMemoryHistory } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App.vue'
import { apiRequest } from './api/client'
import AppHeader from './components/AppHeader.vue'
import MobileNavigation from './components/MobileNavigation.vue'
import { createAppRouter } from './router'
import { useAuthStore } from './stores/auth'
import { useWorkspaceStore } from './stores/workspace'
import LoginView from './views/LoginView.vue'

describe('App', () => {
  afterEach(() => vi.unstubAllGlobals())

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

  it('已登录应用壳同时挂载桌面 Header 与手机导航，由响应式样式决定显示方式', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.initialized = true
    auth.user = { id: 'u-1', username: 'alice', role: 'user', is_active: true }
    const router = createAppRouter(createMemoryHistory())
    await router.push('/')
    await router.isReady()

    const wrapper = mount(App, {
      global: { plugins: [pinia, router, ElementPlus] },
    })

    expect(wrapper.findComponent(AppHeader).exists()).toBe(true)
    expect(wrapper.findComponent(MobileNavigation).exists()).toBe(true)
  })

  it('运行中刷新认证失败后清理工作区并跳转登录页', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    const workspace = useWorkspaceStore()
    auth.initialized = true
    auth.accessToken = 'expired-token'
    auth.user = { id: 'u-1', username: 'alice', role: 'user', is_active: true }
    workspace.knowledgeBases = [{
      id: 'kb-1', name: '用户私有资料', description: null,
      owner_id: 'u-1', owner_username: 'alice',
    }]
    workspace.activeKnowledgeBaseId = 'kb-1'
    vi.spyOn(workspace, 'loadKnowledgeBases').mockResolvedValue()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: { code: 'AUTHENTICATION_REQUIRED', message: '请先登录。' },
    }), { status: 401, headers: { 'Content-Type': 'application/json' } })))
    const router = createAppRouter(createMemoryHistory())
    await router.push('/')
    await router.isReady()
    const wrapper = mount(App, {
      global: { plugins: [pinia, router, ElementPlus] },
    })

    await expect(apiRequest('/api/v1/protected')).rejects.toMatchObject({ status: 401 })
    await flushPromises()

    expect(auth.user).toBeNull()
    expect(workspace.knowledgeBases).toEqual([])
    expect(router.currentRoute.value.fullPath).toBe('/login')
    expect(wrapper.findComponent(LoginView).exists()).toBe(true)
  })
})

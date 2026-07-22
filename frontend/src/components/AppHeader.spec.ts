import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createMemoryHistory } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import { createAppRouter } from '../router'
import { useAuthStore } from '../stores/auth'
import AppHeader from './AppHeader.vue'

describe('AppHeader', () => {
  it('普通用户看到自己的身份且没有管理员入口', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.initialized = true
    auth.user = { id: 'u-1', username: 'alice', role: 'user', is_active: true }
    const router = createAppRouter(createMemoryHistory())
    await router.push('/')
    await router.isReady()

    const wrapper = mount(AppHeader, {
      global: { plugins: [pinia, router, ElementPlus] },
    })

    expect(wrapper.text()).toContain('alice')
    expect(wrapper.text()).toContain('普通用户')
    expect(wrapper.text()).not.toContain('用户管理')
  })

  it('管理员可通过头部入口进入用户管理页', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.initialized = true
    auth.user = { id: 'u-admin', username: 'root', role: 'admin', is_active: true }
    const router = createAppRouter(createMemoryHistory())
    await router.push('/')
    await router.isReady()
    const wrapper = mount(AppHeader, {
      global: { plugins: [pinia, router, ElementPlus] },
    })

    await wrapper.get('[data-test="admin-users-link"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('管理员')
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/admin/users'))
  })

  it('已登录用户可通过头部进入我的页面', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.initialized = true
    auth.user = { id: 'u-1', username: 'alice', role: 'user', is_active: true }
    const router = createAppRouter(createMemoryHistory())
    await router.push('/')
    await router.isReady()
    const wrapper = mount(AppHeader, {
      global: { plugins: [pinia, router, ElementPlus] },
    })

    await wrapper.get('[data-test="profile-link"]').trigger('click')
    await flushPromises()

    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/profile'))
  })

  it('退出后调用认证状态清理并返回登录页', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.initialized = true
    auth.user = { id: 'u-1', username: 'alice', role: 'user', is_active: true }
    const logout = vi.spyOn(auth, 'logout').mockImplementation(async () => {
      auth.user = null
      auth.accessToken = null
    })
    const router = createAppRouter(createMemoryHistory())
    await router.push('/')
    await router.isReady()
    const wrapper = mount(AppHeader, {
      global: { plugins: [pinia, router, ElementPlus] },
    })

    await wrapper.get('[data-test="logout"]').trigger('click')
    await flushPromises()

    expect(logout).toHaveBeenCalledOnce()
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/login'))
  })
})

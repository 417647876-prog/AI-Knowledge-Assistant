import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createMemoryHistory } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import { createAppRouter } from '../router'
import { useAuthStore } from '../stores/auth'
import MobileNavigation from './MobileNavigation.vue'

function setup(role: 'admin' | 'user' = 'user') {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.initialized = true
  auth.user = { id: 'u-1', username: 'alice', role, is_active: true }
  const router = createAppRouter(createMemoryHistory())
  return { auth, pinia, router }
}

describe('MobileNavigation', () => {
  it('普通用户显示当前工作台入口且不暴露管理员入口', async () => {
    const { pinia, router } = setup()
    await router.push('/')
    await router.isReady()
    const wrapper = mount(MobileNavigation, {
      global: { plugins: [pinia, router, ElementPlus] },
    })

    expect(wrapper.get('[data-test="mobile-workspace-link"]').attributes('aria-current')).toBe('page')
    expect(wrapper.find('[data-test="mobile-admin-users-link"]').exists()).toBe(false)
  })

  it('管理员能看到当前用户管理入口', async () => {
    const { pinia, router } = setup('admin')
    await router.push('/admin/users')
    await router.isReady()
    const wrapper = mount(MobileNavigation, {
      global: { plugins: [pinia, router, ElementPlus] },
    })

    expect(wrapper.get('[data-test="mobile-admin-users-link"]').attributes('aria-current')).toBe('page')
  })

  it('普通用户可以从底部导航进入我的页面', async () => {
    const { pinia, router } = setup()
    await router.push('/profile')
    await router.isReady()
    const wrapper = mount(MobileNavigation, {
      global: { plugins: [pinia, router, ElementPlus] },
    })

    expect(wrapper.get('[data-test="mobile-profile-link"]').attributes('aria-current')).toBe('page')
  })

  it('退出时清理认证状态并回到登录页', async () => {
    const { auth, pinia, router } = setup()
    const logout = vi.spyOn(auth, 'logout').mockImplementation(async () => {
      auth.user = null
      auth.accessToken = null
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(MobileNavigation, {
      global: { plugins: [pinia, router, ElementPlus] },
    })

    await wrapper.get('[data-test="mobile-logout"]').trigger('click')
    await flushPromises()

    expect(logout).toHaveBeenCalledOnce()
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/login'))
  })
})

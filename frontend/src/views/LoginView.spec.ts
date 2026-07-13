import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createMemoryHistory } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../api/auth', () => ({
  login: vi.fn(), refresh: vi.fn(), logout: vi.fn(),
}))

import { login } from '../api/auth'
import { ApiError } from '../api/client'
import { createAppRouter } from '../router'
import { useAuthStore } from '../stores/auth'
import LoginView from './LoginView.vue'

describe('LoginView', () => {
  it('提交用户名和密码后登录并进入工作台', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.initialized = true
    vi.mocked(login).mockResolvedValue({
      access_token: 'access-token', token_type: 'bearer', expires_in: 900,
      user: { id: 'u-1', username: 'alice', role: 'user', is_active: true },
    })
    const router = createAppRouter(createMemoryHistory())
    await router.push('/login')
    await router.isReady()
    const wrapper = mount(LoginView, {
      global: { plugins: [pinia, router, ElementPlus] },
    })

    await wrapper.get('input[type="text"]').setValue('alice')
    await wrapper.get('input[type="password"]').setValue('correct-secret')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(login).toHaveBeenCalledWith('alice', 'correct-secret')
    expect(router.currentRoute.value.fullPath).toBe('/')
  })

  it('登录失败时显示后端通用错误消息并留在登录页', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.initialized = true
    vi.mocked(login).mockRejectedValue(new ApiError(
      401, 'INVALID_CREDENTIALS', '用户名或密码错误。', 'req-login-1',
    ))
    const router = createAppRouter(createMemoryHistory())
    await router.push('/login')
    await router.isReady()
    const wrapper = mount(LoginView, {
      global: { plugins: [pinia, router, ElementPlus] },
    })

    await wrapper.get('input[type="text"]').setValue('alice')
    await wrapper.get('input[type="password"]').setValue('wrong-secret')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.get('[data-test="login-error"]').text()).toContain(
      '用户名或密码错误。 [INVALID_CREDENTIALS] 请求标识：req-login-1',
    )
    expect(router.currentRoute.value.fullPath).toBe('/login')
  })
})

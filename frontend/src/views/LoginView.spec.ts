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
import type { AuthSession } from '../types/api'
import LoginView from './LoginView.vue'

describe('LoginView', () => {
  it('为手机键盘和密码管理器提供稳定的表单语义', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.initialized = true
    const router = createAppRouter(createMemoryHistory())
    await router.push('/login')
    await router.isReady()
    const wrapper = mount(LoginView, {
      global: { plugins: [pinia, router, ElementPlus] },
    })

    const username = wrapper.get('[data-test="login-username"]')
    const password = wrapper.get('[data-test="login-password"]')
    expect(username.attributes()).toMatchObject({
      name: 'username', autocomplete: 'username', autocapitalize: 'none', inputmode: 'text',
    })
    expect(password.attributes()).toMatchObject({
      name: 'password', autocomplete: 'current-password', autocapitalize: 'none', inputmode: 'text',
    })
  })

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
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/'))
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

  it('登录请求完成前忽略连续重复提交并保持加载态', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.initialized = true
    let resolveLogin!: (session: AuthSession) => void
    vi.mocked(login).mockReturnValue(new Promise((resolve) => {
      resolveLogin = resolve
    }))
    const router = createAppRouter(createMemoryHistory())
    await router.push('/login')
    await router.isReady()
    const wrapper = mount(LoginView, {
      global: { plugins: [pinia, router, ElementPlus] },
    })
    await wrapper.get('input[type="text"]').setValue('alice')
    await wrapper.get('input[type="password"]').setValue('correct-secret')

    void wrapper.get('form').trigger('submit')
    void wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(login).toHaveBeenCalledOnce()
    expect(wrapper.get('button').classes()).toContain('is-loading')

    resolveLogin({
      access_token: 'access-token', token_type: 'bearer', expires_in: 900,
      user: { id: 'u-1', username: 'alice', role: 'user', is_active: true },
    })
    await flushPromises()

    expect(login).toHaveBeenCalledOnce()
    await vi.waitFor(() => expect(wrapper.get('button').classes()).not.toContain('is-loading'))
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/'))
  })
})

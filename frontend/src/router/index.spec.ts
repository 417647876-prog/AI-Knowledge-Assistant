import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '../stores/auth'
import { createAppRouter } from './index'

describe('router guards', () => {
  it('匿名用户访问工作台时跳转到登录页', async () => {
    setActivePinia(createPinia())
    const auth = useAuthStore()
    vi.spyOn(auth, 'initialize').mockResolvedValue()
    const router = createAppRouter(createMemoryHistory())

    await router.push('/')
    await router.isReady()

    expect(auth.initialize).toHaveBeenCalled()
    expect(router.currentRoute.value.fullPath).toBe('/login')
  })

  it('已登录用户访问登录页时跳转到工作台', async () => {
    setActivePinia(createPinia())
    const auth = useAuthStore()
    auth.user = { id: 'u-1', username: 'alice', role: 'user', is_active: true }
    vi.spyOn(auth, 'initialize').mockResolvedValue()
    const router = createAppRouter(createMemoryHistory())

    await router.push('/login')
    await router.isReady()

    expect(router.currentRoute.value.fullPath).toBe('/')
  })

  it('普通用户访问管理员页时跳转到无权限页', async () => {
    setActivePinia(createPinia())
    const auth = useAuthStore()
    auth.user = { id: 'u-1', username: 'alice', role: 'user', is_active: true }
    vi.spyOn(auth, 'initialize').mockResolvedValue()
    const router = createAppRouter(createMemoryHistory())

    await router.push('/admin/users')
    await router.isReady()

    expect(router.currentRoute.value.fullPath).toBe('/forbidden')
  })

  it('管理员可以进入用户管理页', async () => {
    setActivePinia(createPinia())
    const auth = useAuthStore()
    auth.user = { id: 'u-admin', username: 'root', role: 'admin', is_active: true }
    vi.spyOn(auth, 'initialize').mockResolvedValue()
    const router = createAppRouter(createMemoryHistory())

    await router.push('/admin/users')
    await router.isReady()

    expect(router.currentRoute.value.fullPath).toBe('/admin/users')
  })

  it('已登录普通用户可以停留在无权限页', async () => {
    setActivePinia(createPinia())
    const auth = useAuthStore()
    auth.user = { id: 'u-1', username: 'alice', role: 'user', is_active: true }
    vi.spyOn(auth, 'initialize').mockResolvedValue()
    const router = createAppRouter(createMemoryHistory())

    await router.push('/forbidden')
    await router.isReady()

    expect(router.currentRoute.value.fullPath).toBe('/forbidden')
  })
})

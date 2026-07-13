import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus, { ElDialog } from 'element-plus'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import type { AdminUser } from '../types/api'

const elementMocks = vi.hoisted(() => ({
  confirm: vi.fn().mockResolvedValue(undefined),
  success: vi.fn(),
}))

vi.mock('element-plus', async (importOriginal) => {
  const actual = await importOriginal<typeof import('element-plus')>()
  return {
    ...actual,
    ElMessage: { ...actual.ElMessage, success: elementMocks.success },
    ElMessageBox: { ...actual.ElMessageBox, confirm: elementMocks.confirm },
  }
})

import { useAdminUsersStore } from '../stores/adminUsers'
import AdminUsersView from './AdminUsersView.vue'

const alice: AdminUser = {
  id: 'u-1', username: 'alice', role: 'user', is_active: true,
  created_at: '2026-07-13T08:00:00Z', updated_at: '2026-07-13T08:00:00Z',
}

const mountedWrappers: ReturnType<typeof mount>[] = []

function mountView(loadUsers?: (store: ReturnType<typeof useAdminUsersStore>) => Promise<void>) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useAdminUsersStore()
  store.users = [alice]
  const loadSpy = vi.spyOn(store, 'loadUsers')
  if (loadUsers) loadSpy.mockImplementation(() => loadUsers(store))
  else loadSpy.mockResolvedValue()
  vi.spyOn(store, 'createUser').mockResolvedValue(alice)
  vi.spyOn(store, 'updateUser').mockResolvedValue(alice)
  vi.spyOn(store, 'resetPassword').mockResolvedValue(alice)
  const wrapper = mount(AdminUsersView, {
    attachTo: document.body,
    global: { plugins: [pinia, ElementPlus] },
  })
  mountedWrappers.push(wrapper)
  return { wrapper, store }
}

describe('AdminUsersView', () => {
  beforeEach(() => elementMocks.confirm.mockResolvedValue(undefined))
  afterEach(() => {
    mountedWrappers.splice(0).forEach((wrapper) => wrapper.unmount())
    document.body.innerHTML = ''
  })

  it('展示用户字段并在挂载时加载列表', async () => {
    const { wrapper, store } = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('用户管理')
    expect(wrapper.text()).toContain('alice')
    expect(wrapper.text()).toContain('普通用户')
    expect(wrapper.text()).toContain('启用')
    expect(wrapper.text()).toContain('2026')
    expect(store.loadUsers).toHaveBeenCalledOnce()
  })

  it('确认后创建用户，防重复提交并清空密码', async () => {
    const { wrapper, store } = mountView()
    await wrapper.get('[data-test="create-user"]').trigger('click')
    await wrapper.get('[data-test="username"]').setValue('bob')
    await wrapper.get('[data-test="password"]').setValue('temporary pass 123')

    await wrapper.get('[data-test="submit-user"]').trigger('click')
    await flushPromises()

    expect(elementMocks.confirm).toHaveBeenCalled()
    expect(store.createUser).toHaveBeenCalledWith({
      username: 'bob', password: 'temporary pass 123', role: 'user',
    })
    expect((wrapper.get('[data-test="password"]').element as HTMLInputElement).value).toBe('')
    expect(elementMocks.success).toHaveBeenCalledWith('用户创建成功。')
  })

  it('创建请求完成前忽略连续提交并保持 loading', async () => {
    const { wrapper, store } = mountView()
    let resolveCreate!: (user: AdminUser) => void
    vi.mocked(store.createUser).mockReturnValue(new Promise((resolve) => {
      resolveCreate = resolve
    }))
    await wrapper.get('[data-test="create-user"]').trigger('click')
    await wrapper.get('[data-test="username"]').setValue('bob')
    await wrapper.get('[data-test="password"]').setValue('temporary pass 123')

    void wrapper.get('[data-test="submit-user"]').trigger('click')
    void wrapper.get('[data-test="submit-user"]').trigger('click')
    await flushPromises()

    expect(store.createUser).toHaveBeenCalledOnce()
    expect(wrapper.get('[data-test="submit-user"]').classes()).toContain('is-loading')

    resolveCreate(alice)
    await flushPromises()
    expect(store.createUser).toHaveBeenCalledOnce()
  })

  it('初始加载完成前禁用所有 mutation，完成后创建结果不会被旧列表覆盖', async () => {
    let resolveLoad!: () => void
    const { wrapper, store } = mountView((currentStore) => {
      currentStore.loading = true
      return new Promise<void>((resolve) => {
        resolveLoad = () => {
          currentStore.loading = false
          resolve()
        }
      })
    })
    vi.mocked(store.createUser).mockImplementation(async () => {
      const bob = { ...alice, id: 'u-2', username: 'bob' }
      store.users.push(bob)
      return bob
    })
    await flushPromises()

    expect(wrapper.get('[data-test="create-user"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-test="role-mobile-u-1"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-test="status-mobile-u-1"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-test="reset-mobile-u-1"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-test="create-user"]').trigger('click')
    expect(wrapper.find('[data-test="username"]').exists()).toBe(false)

    resolveLoad()
    await flushPromises()
    await wrapper.get('[data-test="create-user"]').trigger('click')
    await wrapper.get('[data-test="username"]').setValue('bob')
    await wrapper.get('[data-test="password"]').setValue('temporary pass 123')
    await wrapper.get('[data-test="submit-user"]').trigger('click')
    await flushPromises()

    expect(store.createUser).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('bob')
  })

  it('弹窗已打开后开始 load 时，创建和重置都不能提交', async () => {
    const { wrapper, store } = mountView()
    await wrapper.get('[data-test="create-user"]').trigger('click')
    await wrapper.get('[data-test="username"]').setValue('bob')
    await wrapper.get('[data-test="password"]').setValue('temporary pass 123')
    store.loading = true
    await flushPromises()

    const createSubmit = wrapper.get('[data-test="submit-user"]')
    expect(createSubmit.attributes('disabled')).toBeDefined()
    createSubmit.element.closest('form')!.dispatchEvent(new Event('submit', {
      bubbles: true, cancelable: true,
    }))
    await flushPromises()
    expect(elementMocks.confirm).not.toHaveBeenCalled()
    expect(store.createUser).not.toHaveBeenCalled()

    store.loading = false
    await wrapper.get('[data-test="cancel-create"]').trigger('click')
    await wrapper.get('[data-test="reset-mobile-u-1"]').trigger('click')
    await wrapper.get('[data-test="reset-password"]').setValue('replacement pass 123')
    store.loading = true
    await flushPromises()

    const resetSubmit = wrapper.get('[data-test="submit-reset"]')
    expect(resetSubmit.attributes('disabled')).toBeDefined()
    resetSubmit.element.closest('form')!.dispatchEvent(new Event('submit', {
      bubbles: true, cancelable: true,
    }))
    await flushPromises()
    expect(elementMocks.confirm).not.toHaveBeenCalled()
    expect(store.resetPassword).not.toHaveBeenCalled()
  })

  it('创建输入不符合后端边界时显示清晰提示且不发请求', async () => {
    const { wrapper, store } = mountView()
    await wrapper.get('[data-test="create-user"]').trigger('click')
    await wrapper.get('[data-test="username"]').setValue('invalid name')
    await wrapper.get('[data-test="password"]').setValue('too-short')

    await wrapper.get('[data-test="submit-user"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-test="create-user-error"]').text()).toContain(
      '用户名需为 3–50 位，只能包含英文字母、数字、点、下划线和连字符。',
    )
    expect(elementMocks.confirm).not.toHaveBeenCalled()
    expect(store.createUser).not.toHaveBeenCalled()
  })

  it('创建失败时在弹窗显示后端错误', async () => {
    const { wrapper, store } = mountView()
    vi.mocked(store.createUser).mockRejectedValue(new ApiError(
      409, 'USERNAME_ALREADY_EXISTS', '用户名已存在。', 'req-create-1',
    ))
    await wrapper.get('[data-test="create-user"]').trigger('click')
    await wrapper.get('[data-test="username"]').setValue('alice')
    await wrapper.get('[data-test="password"]').setValue('temporary pass 123')

    await wrapper.get('[data-test="submit-user"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-test="create-user-error"]').text()).toContain(
      '用户名已存在。 [USERNAME_ALREADY_EXISTS] 请求标识：req-create-1',
    )
  })

  it('创建确认取消时不调用 API', async () => {
    const { wrapper, store } = mountView()
    elementMocks.confirm.mockRejectedValueOnce('cancel')
    await wrapper.get('[data-test="create-user"]').trigger('click')
    await wrapper.get('[data-test="username"]').setValue('bob')
    await wrapper.get('[data-test="password"]').setValue('temporary pass 123')

    await wrapper.get('[data-test="submit-user"]').trigger('click')
    await flushPromises()

    expect(store.createUser).not.toHaveBeenCalled()
    expect(wrapper.find('[data-test="create-user-error"]').exists()).toBe(false)
  })

  it('创建弹窗关闭后统一清空表单，重新打开不恢复密码', async () => {
    const { wrapper } = mountView()
    await wrapper.get('[data-test="create-user"]').trigger('click')
    const password = wrapper.get('[data-test="password"]')
    await wrapper.get('[data-test="username"]').setValue('bob')
    await password.setValue('temporary pass 123')

    await wrapper.get('[data-test="cancel-create"]').trigger('click')
    await flushPromises()
    expect((password.element as HTMLInputElement).value).toBe('')

    await wrapper.get('[data-test="create-user"]').trigger('click')
    expect((wrapper.get('[data-test="password"]').element as HTMLInputElement).value).toBe('')
  })

  it('通过弹窗 X 关闭创建表单时也清空密码', async () => {
    const { wrapper } = mountView()
    await wrapper.get('[data-test="create-user"]').trigger('click')
    const password = wrapper.get('[data-test="password"]')
    await password.setValue('temporary pass 123')

    wrapper.findAllComponents(ElDialog)[0]!.vm.$emit('update:modelValue', false)
    await flushPromises()

    expect((password.element as HTMLInputElement).value).toBe('')
  })

  it('确认后切换角色和停用用户', async () => {
    const { wrapper, store } = mountView()
    await wrapper.get('[data-test="role-mobile-u-1"]').trigger('click')
    await flushPromises()
    expect(store.updateUser).toHaveBeenCalledWith('u-1', { role: 'admin' })

    await wrapper.get('[data-test="status-mobile-u-1"]').trigger('click')
    await flushPromises()
    expect(store.updateUser).toHaveBeenCalledWith('u-1', { is_active: false })
    expect(elementMocks.confirm).toHaveBeenCalledTimes(2)
  })

  it('重置密码需要确认，成功后清空密码且不回显', async () => {
    const { wrapper, store } = mountView()
    await wrapper.get('[data-test="reset-mobile-u-1"]').trigger('click')
    const password = wrapper.get('[data-test="reset-password"]')
    expect(password.attributes('type')).toBe('password')
    await password.setValue('replacement pass 123')

    await wrapper.get('[data-test="submit-reset"]').trigger('click')
    await flushPromises()

    expect(store.resetPassword).toHaveBeenCalledWith('u-1', 'replacement pass 123')
    expect((password.element as HTMLInputElement).value).toBe('')
    expect(elementMocks.success).toHaveBeenCalledWith('密码重置成功。')
  })

  it('重置密码过短时显示清晰提示且不发请求', async () => {
    const { wrapper, store } = mountView()
    await wrapper.get('[data-test="reset-mobile-u-1"]').trigger('click')
    await wrapper.get('[data-test="reset-password"]').setValue('too-short')

    await wrapper.get('[data-test="submit-reset"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-test="reset-password-error"]').text()).toContain(
      '密码长度需为 12–128 个字符。',
    )
    expect(elementMocks.confirm).not.toHaveBeenCalled()
    expect(store.resetPassword).not.toHaveBeenCalled()
  })

  it('重置密码失败时在弹窗显示后端错误', async () => {
    const { wrapper, store } = mountView()
    vi.mocked(store.resetPassword).mockRejectedValue(new ApiError(
      404, 'USER_NOT_FOUND', '用户不存在。', 'req-reset-1',
    ))
    await wrapper.get('[data-test="reset-mobile-u-1"]').trigger('click')
    await wrapper.get('[data-test="reset-password"]').setValue('replacement pass 123')

    await wrapper.get('[data-test="submit-reset"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-test="reset-password-error"]').text()).toContain(
      '用户不存在。 [USER_NOT_FOUND] 请求标识：req-reset-1',
    )
  })

  it('重置密码弹窗关闭后清空密码，重新打开不恢复', async () => {
    const { wrapper } = mountView()
    await wrapper.get('[data-test="reset-mobile-u-1"]').trigger('click')
    const password = wrapper.get('[data-test="reset-password"]')
    await password.setValue('replacement pass 123')

    await wrapper.get('[data-test="cancel-reset"]').trigger('click')
    await flushPromises()
    expect((password.element as HTMLInputElement).value).toBe('')

    await wrapper.get('[data-test="reset-mobile-u-1"]').trigger('click')
    expect((wrapper.get('[data-test="reset-password"]').element as HTMLInputElement).value).toBe('')
  })

  it.each([
    {
      selector: '[data-test="role-mobile-u-1"]',
      code: 'LAST_ADMIN_REQUIRED',
      message: '系统必须保留至少一个启用的管理员。',
    },
    {
      selector: '[data-test="status-mobile-u-1"]',
      code: 'CANNOT_DEACTIVATE_SELF',
      message: '管理员不能停用自己的账号。',
    },
  ])('原样友好展示后端保护错误 $code', async ({ selector, code, message }) => {
    const { wrapper, store } = mountView()
    vi.mocked(store.updateUser).mockRejectedValue(new ApiError(
      409, code, message, 'req-admin-1',
    ))

    await wrapper.get(selector).trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-test="admin-users-error"]').text()).toContain(
      `${message} [${code}] 请求标识：req-admin-1`,
    )
  })
})

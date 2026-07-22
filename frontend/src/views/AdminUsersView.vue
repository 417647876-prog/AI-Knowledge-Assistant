<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { formatApiError } from '../api/client'
import { useAdminUsersStore } from '../stores/adminUsers'
import type { AdminUser, UserRole } from '../types/api'

const store = useAdminUsersStore()
const pageError = ref<string | null>(null)
const createVisible = ref(false)
const resetVisible = ref(false)
const quotaVisible = ref(false)
const submittingCreate = ref(false)
const submittingReset = ref(false)
const loadingQuota = ref(false)
const submittingQuota = ref(false)
const pendingUserIds = ref(new Set<string>())
const createForm = reactive({ username: '', password: '', role: 'user' as UserRole })
const resetForm = reactive({ userId: '', username: '', password: '' })
const quotaForm = reactive({
  userId: '', username: '',
  daily_question_limit: null as number | null,
  daily_upload_limit: null as number | null,
  storage_bytes_limit: null as number | null,
})

function showError(error: unknown): void {
  pageError.value = formatApiError(error)
}

function formatCreatedAt(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}

function validateUsername(username: string): string | null {
  const normalized = username.trim()
  if (normalized.length < 3 || normalized.length > 50 || !/^[A-Za-z0-9._-]+$/.test(normalized)) {
    return '用户名需为 3–50 位，只能包含英文字母、数字、点、下划线和连字符。'
  }
  return null
}

function validatePassword(password: string): string | null {
  return password.length < 6 || password.length > 128
    ? '密码长度需为 6–128 个字符。'
    : null
}

function openCreate(): void {
  if (store.loading) return
  pageError.value = null
  createVisible.value = true
}

function resetCreateForm(): void {
  createForm.username = ''
  createForm.password = ''
  createForm.role = 'user'
  pageError.value = null
}

async function submitCreate(): Promise<void> {
  if (submittingCreate.value || store.loading) return
  pageError.value = null
  const validationError = validateUsername(createForm.username)
    ?? validatePassword(createForm.password)
  if (validationError) {
    pageError.value = validationError
    return
  }
  submittingCreate.value = true
  try {
    await ElMessageBox.confirm(
      `确认创建用户“${createForm.username}”吗？`,
      '创建用户确认',
      { confirmButtonText: '确认创建', cancelButtonText: '取消', type: 'warning' },
    )
    await store.createUser({ ...createForm, username: createForm.username.trim() })
    createVisible.value = false
    ElMessage.success('用户创建成功。')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') showError(error)
  } finally {
    submittingCreate.value = false
  }
}

async function updateUser(user: AdminUser, input: { role?: UserRole; is_active?: boolean }) {
  if (store.loading || pendingUserIds.value.has(user.id)) return
  const description = input.role
    ? `确认将“${user.username}”设为${input.role === 'admin' ? '管理员' : '普通用户'}吗？`
    : `确认${input.is_active ? '启用' : '停用'}用户“${user.username}”吗？`
  pageError.value = null
  pendingUserIds.value.add(user.id)
  try {
    await ElMessageBox.confirm(description, '修改用户确认', {
      confirmButtonText: '确认修改', cancelButtonText: '取消', type: 'warning',
    })
    await store.updateUser(user.id, input)
    ElMessage.success('用户信息已更新。')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') showError(error)
  } finally {
    pendingUserIds.value.delete(user.id)
  }
}

function openReset(user: AdminUser): void {
  if (store.loading) return
  pageError.value = null
  resetForm.userId = user.id
  resetForm.username = user.username
  resetForm.password = ''
  resetVisible.value = true
}

function resetPasswordForm(): void {
  resetForm.userId = ''
  resetForm.username = ''
  resetForm.password = ''
  pageError.value = null
}

watch(createVisible, (visible) => {
  if (!visible) resetCreateForm()
}, { flush: 'sync' })

watch(resetVisible, (visible) => {
  if (!visible) resetPasswordForm()
}, { flush: 'sync' })

async function submitReset(): Promise<void> {
  if (submittingReset.value || store.loading) return
  pageError.value = null
  const validationError = validatePassword(resetForm.password)
  if (validationError) {
    pageError.value = validationError
    return
  }
  submittingReset.value = true
  try {
    await ElMessageBox.confirm(
      `确认重置用户“${resetForm.username}”的密码吗？该用户现有会话将失效。`,
      '重置密码确认',
      { confirmButtonText: '确认重置', cancelButtonText: '取消', type: 'warning' },
    )
    await store.resetPassword(resetForm.userId, resetForm.password)
    resetVisible.value = false
    ElMessage.success('密码重置成功。')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') showError(error)
  } finally {
    submittingReset.value = false
  }
}

onMounted(async () => {
  try { await store.loadUsers() } catch (error) { showError(error) }
})

function resetQuotaForm(): void {
  quotaForm.userId = ''
  quotaForm.username = ''
  quotaForm.daily_question_limit = null
  quotaForm.daily_upload_limit = null
  quotaForm.storage_bytes_limit = null
  pageError.value = null
}

watch(quotaVisible, (visible) => {
  if (!visible) resetQuotaForm()
}, { flush: 'sync' })

async function openQuota(user: AdminUser): Promise<void> {
  if (store.loading || loadingQuota.value) return
  pageError.value = null
  quotaForm.userId = user.id
  quotaForm.username = user.username
  quotaVisible.value = true
  loadingQuota.value = true
  try {
    const quota = await store.loadQuota(user.id)
    if (quotaForm.userId !== user.id) return
    quotaForm.daily_question_limit = quota.daily_question_limit
    quotaForm.daily_upload_limit = quota.daily_upload_limit
    quotaForm.storage_bytes_limit = quota.storage_bytes_limit
  } catch (error) {
    showError(error)
  } finally {
    loadingQuota.value = false
  }
}

function setQuotaField(
  field: 'daily_question_limit' | 'daily_upload_limit' | 'storage_bytes_limit',
  event: Event,
): void {
  const value = (event.target as HTMLInputElement).value
  quotaForm[field] = value === '' ? null : Number(value)
}

async function submitQuota(): Promise<void> {
  if (!quotaForm.userId || loadingQuota.value || submittingQuota.value) return
  const values = [
    quotaForm.daily_question_limit,
    quotaForm.daily_upload_limit,
    quotaForm.storage_bytes_limit,
  ]
  if (values.some((value) => value !== null && (!Number.isInteger(value) || value < 0))) {
    pageError.value = '额度必须是大于或等于 0 的整数，留空表示使用系统默认值。'
    return
  }
  submittingQuota.value = true
  pageError.value = null
  try {
    await ElMessageBox.confirm(`确认调整用户“${quotaForm.username}”的额度吗？`, '调整额度确认', {
      confirmButtonText: '确认调整', cancelButtonText: '取消', type: 'warning',
    })
    await store.updateQuota(quotaForm.userId, {
      daily_question_limit: quotaForm.daily_question_limit,
      daily_upload_limit: quotaForm.daily_upload_limit,
      storage_bytes_limit: quotaForm.storage_bytes_limit,
    })
    quotaVisible.value = false
    ElMessage.success('用户额度已更新。')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') showError(error)
  } finally {
    submittingQuota.value = false
  }
}
</script>

<template>
  <main class="admin-users-page">
    <section class="workspace-card admin-users-card">
      <div class="admin-users-heading">
        <div>
          <h2>用户管理</h2>
          <p>创建账号、调整角色和状态，或重置用户密码。</p>
        </div>
        <el-button
          data-test="create-user"
          type="primary"
          :disabled="store.loading"
          @click="openCreate"
        >
          创建用户
        </el-button>
      </div>

      <p
        v-if="pageError && !createVisible && !resetVisible && !quotaVisible"
        data-test="admin-users-error"
        class="admin-users-error"
      >
        {{ pageError }}
      </p>

      <div class="admin-users-table" data-test="users-table">
        <el-table v-loading="store.loading" :data="store.users" empty-text="暂无用户">
          <el-table-column prop="username" label="用户名" min-width="130" />
          <el-table-column label="角色" min-width="110">
            <template #default="{ row }">
              {{ row.role === 'admin' ? '管理员' : '普通用户' }}
            </template>
          </el-table-column>
          <el-table-column label="状态" min-width="90">
            <template #default="{ row }">
              <el-tag :type="row.is_active ? 'success' : 'info'">
                {{ row.is_active ? '启用' : '停用' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="创建时间" min-width="190">
            <template #default="{ row }">{{ formatCreatedAt(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" min-width="300" fixed="right">
            <template #default="{ row }">
              <div class="admin-user-actions">
                <el-button
                  :data-test="`role-${row.id}`"
                  :disabled="store.loading"
                  :loading="pendingUserIds.has(row.id)"
                  @click="updateUser(row, { role: row.role === 'admin' ? 'user' : 'admin' })"
                >
                  设为{{ row.role === 'admin' ? '普通用户' : '管理员' }}
                </el-button>
                <el-button
                  :data-test="`status-${row.id}`"
                  :disabled="store.loading"
                  :loading="pendingUserIds.has(row.id)"
                  @click="updateUser(row, { is_active: !row.is_active })"
                >
                  {{ row.is_active ? '停用' : '启用' }}
                </el-button>
                <el-button
                  :data-test="`quota-${row.id}`"
                  :disabled="store.loading"
                  @click="openQuota(row)"
                >
                  调整额度
                </el-button>
                <el-button
                  :data-test="`reset-${row.id}`"
                  :disabled="store.loading"
                  @click="openReset(row)"
                >
                  重置密码
                </el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="admin-users-mobile" data-test="users-mobile">
        <article v-for="user in store.users" :key="user.id" class="admin-user-card">
          <div class="admin-user-card-title">
            <strong>{{ user.username }}</strong>
            <el-tag :type="user.is_active ? 'success' : 'info'">
              {{ user.is_active ? '启用' : '停用' }}
            </el-tag>
          </div>
          <dl>
            <dt>角色</dt><dd>{{ user.role === 'admin' ? '管理员' : '普通用户' }}</dd>
            <dt>创建时间</dt><dd>{{ formatCreatedAt(user.created_at) }}</dd>
          </dl>
          <div class="admin-user-actions">
            <el-button
              :data-test="`role-mobile-${user.id}`"
              :disabled="store.loading"
              :loading="pendingUserIds.has(user.id)"
              @click="updateUser(user, { role: user.role === 'admin' ? 'user' : 'admin' })"
            >切换角色</el-button>
            <el-button
              :data-test="`status-mobile-${user.id}`"
              :disabled="store.loading"
              :loading="pendingUserIds.has(user.id)"
              @click="updateUser(user, { is_active: !user.is_active })"
            >{{ user.is_active ? '停用' : '启用' }}</el-button>
            <el-button
              :data-test="`quota-mobile-${user.id}`"
              :disabled="store.loading"
              @click="openQuota(user)"
            >
              调整额度
            </el-button>
            <el-button
              :data-test="`reset-mobile-${user.id}`"
              :disabled="store.loading"
              @click="openReset(user)"
            >
              重置密码
            </el-button>
          </div>
        </article>
      </div>
    </section>

    <el-dialog
      v-model="createVisible"
      title="创建用户"
      width="min(92vw, 520px)"
      @close="resetCreateForm"
    >
      <p v-if="pageError" data-test="create-user-error" class="admin-users-error">
        {{ pageError }}
      </p>
      <el-form label-position="top" @submit.prevent="submitCreate">
        <el-form-item label="用户名">
          <el-input data-test="username" v-model="createForm.username" maxlength="50" />
        </el-form-item>
        <el-form-item label="临时密码">
          <el-input
            data-test="password"
            v-model="createForm.password"
            type="password"
            maxlength="128"
            autocomplete="new-password"
            show-password
          />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="createForm.role" data-test="create-role">
            <el-option label="普通用户" value="user" />
            <el-option label="管理员" value="admin" />
          </el-select>
        </el-form-item>
        <el-button
          data-test="submit-user"
          native-type="submit"
          type="primary"
          :disabled="store.loading"
          :loading="submittingCreate"
        >确认创建</el-button>
        <el-button
          data-test="cancel-create"
          :disabled="submittingCreate"
          @click="createVisible = false"
        >取消</el-button>
      </el-form>
    </el-dialog>

    <el-dialog
      v-model="resetVisible"
      title="重置密码"
      width="min(92vw, 520px)"
      @close="resetPasswordForm"
    >
      <p>为用户“{{ resetForm.username }}”设置新密码。</p>
      <p v-if="pageError" data-test="reset-password-error" class="admin-users-error">
        {{ pageError }}
      </p>
      <el-form label-position="top" @submit.prevent="submitReset">
        <el-form-item label="新密码">
          <el-input
            data-test="reset-password"
            v-model="resetForm.password"
            type="password"
            maxlength="128"
            autocomplete="new-password"
            show-password
          />
        </el-form-item>
        <el-button
          data-test="submit-reset"
          native-type="submit"
          type="primary"
          :disabled="store.loading"
          :loading="submittingReset"
        >确认重置</el-button>
        <el-button
          data-test="cancel-reset"
          :disabled="submittingReset"
          @click="resetVisible = false"
        >取消</el-button>
      </el-form>
    </el-dialog>

    <el-dialog
      v-model="quotaVisible"
      title="调整用户额度"
      width="min(92vw, 520px)"
      @close="resetQuotaForm"
    >
      <p>用户：{{ quotaForm.username }}</p>
      <p class="quota-form-note">留空表示使用系统默认值，填写 0 表示禁用对应额度。</p>
      <p v-if="pageError" class="admin-users-error" role="alert">{{ pageError }}</p>
      <form class="quota-form-grid" @submit.prevent="submitQuota">
        <label>
          每日问答次数
          <input
            data-test="quota-questions"
            type="number"
            min="0"
            step="1"
            :value="quotaForm.daily_question_limit ?? ''"
            @input="setQuotaField('daily_question_limit', $event)"
          >
        </label>
        <label>
          每日上传次数
          <input
            data-test="quota-uploads"
            type="number"
            min="0"
            step="1"
            :value="quotaForm.daily_upload_limit ?? ''"
            @input="setQuotaField('daily_upload_limit', $event)"
          >
        </label>
        <label>
          存储字节数
          <input
            data-test="quota-storage"
            type="number"
            min="0"
            step="1"
            :value="quotaForm.storage_bytes_limit ?? ''"
            @input="setQuotaField('storage_bytes_limit', $event)"
          >
        </label>
        <div>
          <el-button
            data-test="submit-quota"
            native-type="submit"
            type="primary"
            :disabled="loadingQuota || store.loading"
            :loading="submittingQuota"
          >确认调整</el-button>
          <el-button :disabled="submittingQuota" @click="quotaVisible = false">取消</el-button>
        </div>
      </form>
    </el-dialog>
  </main>
</template>

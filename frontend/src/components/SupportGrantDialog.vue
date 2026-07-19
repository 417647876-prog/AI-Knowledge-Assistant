<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { formatApiError } from '../api/client'
import {
  createSupportGrant,
  listSupportAdministrators,
  listSupportGrants,
  revokeSupportGrant,
} from '../api/supportGrants'
import type { SupportAdministrator, SupportGrant } from '../types/api'

const props = defineProps<{ modelValue: boolean; knowledgeBaseId: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
const administrators = ref<SupportAdministrator[]>([])
const grants = ref<SupportGrant[]>([])
const selectedAdministratorId = ref('')
const expiresInMinutes = ref(30)
const loading = ref(false)
const submitting = ref(false)
const visible = computed({ get: () => props.modelValue, set: (value: boolean) => emit('update:modelValue', value) })

async function load(): Promise<void> {
  loading.value = true
  try {
    const [loadedAdministrators, loadedGrants] = await Promise.all([
      listSupportAdministrators(), listSupportGrants(props.knowledgeBaseId),
    ])
    administrators.value = loadedAdministrators
    grants.value = loadedGrants
    if (!selectedAdministratorId.value) selectedAdministratorId.value = loadedAdministrators[0]?.id ?? ''
  } catch (error) {
    ElMessage.error(formatApiError(error))
  } finally { loading.value = false }
}

async function create(): Promise<void> {
  if (!selectedAdministratorId.value || submitting.value) return
  submitting.value = true
  try {
    await createSupportGrant(props.knowledgeBaseId, {
      admin_user_id: selectedAdministratorId.value,
      expires_in_minutes: expiresInMinutes.value,
    })
    ElMessage.success('已创建只读支持授权。')
    await load()
  } catch (error) {
    ElMessage.error(formatApiError(error))
  } finally { submitting.value = false }
}

async function revoke(grant: SupportGrant): Promise<void> {
  try {
    await revokeSupportGrant(grant.id)
    ElMessage.success('已撤销支持授权。')
    await load()
  } catch (error) { ElMessage.error(formatApiError(error)) }
}

watch(visible, (value) => { if (value) void load() }, { immediate: true })
</script>

<template>
  <el-dialog v-model="visible" title="支持授权" width="min(92vw, 520px)" destroy-on-close>
    <p>仅允许指定管理员在有效期内只读查看当前知识库，不能上传、删除或修改内容。</p>
    <el-form v-loading="loading" label-position="top" @submit.prevent="create">
      <el-form-item label="支持管理员">
        <el-select v-model="selectedAdministratorId" data-test="support-administrator" :disabled="loading">
          <el-option v-for="administrator in administrators" :key="administrator.id" :label="administrator.username" :value="administrator.id" />
        </el-select>
      </el-form-item>
      <el-form-item label="有效分钟数">
        <el-input-number v-model="expiresInMinutes" :min="5" :max="120" />
      </el-form-item>
      <el-button data-test="create-support-grant" native-type="submit" type="primary" :disabled="!selectedAdministratorId" :loading="submitting">创建只读授权</el-button>
    </el-form>
    <section class="support-grant-list" aria-label="当前支持授权">
      <p v-if="!grants.length">当前没有支持授权。</p>
      <article v-for="grant in grants" :key="grant.id" class="support-grant-item">
        <span>管理员 ID：{{ grant.admin_user_id }}</span>
        <span>到期：{{ new Date(grant.expires_at).toLocaleString('zh-CN') }}</span>
        <el-button link type="danger" @click="revoke(grant)">撤销</el-button>
      </article>
    </section>
  </el-dialog>
</template>

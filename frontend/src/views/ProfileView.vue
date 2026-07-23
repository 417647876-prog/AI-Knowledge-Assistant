<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { formatApiError } from '../api/client'
import { useAuthStore } from '../stores/auth'
import { useProfileStore } from '../stores/profile'
import type { FeedbackReason } from '../types/api'

const auth = useAuthStore()
const profile = useProfileStore()
const deletingMessageId = ref<string | null>(null)
const pageError = ref<string | null>(null)

const limits = computed(() => {
  if (!profile.quota) return null
  const { defaults, overrides } = profile.quota
  return {
    questions: overrides.daily_question_limit ?? defaults.daily_question_limit,
    uploads: overrides.daily_upload_limit ?? defaults.daily_upload_limit,
    storage: overrides.storage_bytes_limit ?? defaults.storage_bytes_limit,
  }
})

const usageUnknown = computed(() => (profile.usage?.usage_unknown_count ?? 0) > 0)

const reasonLabels: Record<FeedbackReason, string> = {
  helpful_clear: '回答清晰',
  helpful_cited: '引用有帮助',
  unhelpful_wrong: '内容不正确',
  unhelpful_missing: '缺少关键信息',
  unhelpful_unclear: '表达不清晰',
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${Number((bytes / 1024 ** 3).toFixed(2))} GB`
  if (bytes >= 1024 ** 2) return `${Number((bytes / 1024 ** 2).toFixed(2))} MB`
  if (bytes >= 1024) return `${Number((bytes / 1024).toFixed(2))} KB`
  return `${bytes} B`
}

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short', hour12: false,
    }).format(date)
}

async function load(): Promise<void> {
  if (!auth.user) return
  pageError.value = null
  try {
    await profile.load(auth.user.id)
  } catch (error) {
    pageError.value = formatApiError(error)
  }
}

async function changeFeedbackPage(page: number): Promise<void> {
  pageError.value = null
  try {
    await profile.loadFeedback(page)
  } catch (error) {
    pageError.value = formatApiError(error)
  }
}

async function deleteFeedback(messageId: string): Promise<void> {
  deletingMessageId.value = messageId
  pageError.value = null
  try {
    await ElMessageBox.confirm('确认删除这条反馈吗？此操作不会删除会话消息。', '删除反馈', {
      confirmButtonText: '确认删除', cancelButtonText: '取消', type: 'warning',
    })
    await profile.deleteFeedback(messageId)
    ElMessage.success('反馈已删除。')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') pageError.value = formatApiError(error)
  } finally {
    deletingMessageId.value = null
  }
}

onMounted(load)
</script>

<template>
  <main class="profile-page">
    <header class="profile-heading">
      <div>
        <h2>我的</h2>
        <p>查看今日额度、模型用量和自己提交的反馈。</p>
      </div>
      <el-button :loading="profile.loading" @click="load">刷新</el-button>
    </header>

    <p v-if="pageError" class="profile-error" role="alert" data-test="profile-error">
      {{ pageError }}
    </p>

    <section class="workspace-card profile-section" aria-labelledby="quota-title">
      <div class="profile-section-heading">
        <div>
          <h3 id="quota-title">今日额度</h3>
          <p v-if="profile.resetAt" data-test="quota-reset">
            下次重置：{{ formatDate(profile.resetAt) }}（上海时间）
          </p>
        </div>
      </div>
      <div v-if="profile.quota && limits" class="quota-summary" data-test="quota-summary">
        <article>
          <span>问答</span>
          <strong>{{ profile.quota.used.question_count }} / {{ limits.questions }}</strong>
          <small>剩余 {{ profile.quota.remaining.question_count }} 次</small>
        </article>
        <article>
          <span>上传</span>
          <strong>{{ profile.quota.used.upload_count }} / {{ limits.uploads }}</strong>
          <small>剩余 {{ profile.quota.remaining.upload_count }} 次</small>
        </article>
        <article>
          <span>存储</span>
          <strong>{{ formatBytes(profile.quota.used.storage_bytes_used) }} / {{ formatBytes(limits.storage) }}</strong>
          <small>剩余 {{ formatBytes(profile.quota.remaining.storage_bytes) }}</small>
        </article>
      </div>
      <p v-else-if="!profile.loading" class="profile-empty">暂无额度数据。</p>
    </section>

    <section class="workspace-card profile-section" aria-labelledby="usage-title">
      <div class="profile-section-heading">
        <div>
          <h3 id="usage-title">今日模型用量</h3>
          <p>统计范围与每日额度使用同一上海自然日。</p>
        </div>
      </div>
      <div v-if="profile.usage" class="usage-summary">
        <article>
          <span>Token</span>
          <strong data-test="usage-tokens">
            {{ usageUnknown ? '未知' : profile.usage.tokens.total_tokens.toLocaleString('zh-CN') }}
          </strong>
        </article>
        <article>
          <span>估算费用</span>
          <strong data-test="usage-cost">
            {{ usageUnknown ? '未知' : `¥${profile.usage.estimated_cost}` }}
          </strong>
        </article>
        <p v-if="usageUnknown" class="usage-unknown" role="status">
          有 {{ profile.usage.usage_unknown_count }} 条调用缺少完整用量，Token 和费用不按 0 展示。
        </p>
      </div>
      <p v-else-if="!profile.loading" class="profile-empty">暂无用量数据。</p>
    </section>

    <section class="workspace-card profile-section" aria-labelledby="feedback-title">
      <div class="profile-section-heading">
        <div>
          <h3 id="feedback-title">我的反馈</h3>
          <p>这里只显示当前账号提交的反馈，不展示问题或回答正文。</p>
        </div>
      </div>
      <div v-loading="profile.feedbackLoading" class="feedback-list">
        <article v-for="item in profile.feedback.items" :key="item.id" class="feedback-item">
          <div>
            <strong>{{ item.helpful ? '有帮助' : '待改进' }}</strong>
            <span>{{ item.reason ? reasonLabels[item.reason] : '未选择原因' }}</span>
            <small>{{ formatDate(item.updated_at) }}</small>
          </div>
          <el-button
            :data-test="`delete-feedback-${item.message_id}`"
            :loading="deletingMessageId === item.message_id"
            type="danger"
            plain
            @click="deleteFeedback(item.message_id)"
          >
            删除反馈
          </el-button>
        </article>
        <p v-if="profile.feedback.items.length === 0" class="profile-empty">还没有提交反馈。</p>
      </div>
      <el-pagination
        v-if="profile.feedback.total > profile.feedback.page_size"
        class="profile-pagination"
        layout="prev, pager, next"
        :current-page="profile.feedback.page"
        :page-size="profile.feedback.page_size"
        :total="profile.feedback.total"
        @current-change="changeFeedbackPage"
      />
    </section>
  </main>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { formatApiError } from '../api/client'
import { useAdminOperationsStore } from '../stores/adminOperations'

const store = useAdminOperationsStore()
const pageError = ref<string | null>(null)
const now = new Date()
const sevenDaysAgo = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000)
const filters = reactive({
  start: sevenDaysAgo.toISOString().slice(0, 16),
  end: now.toISOString().slice(0, 16),
})

const metricLabels: Record<string, string> = {
  observation_total: '观测数',
  refused_total: '拒答数',
  valid_citation_total: '有效引用数',
  total_duration_ms: '累计耗时（毫秒）',
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${Number((bytes / 1024 ** 3).toFixed(2))} GB`
  if (bytes >= 1024 ** 2) return `${Number((bytes / 1024 ** 2).toFixed(2))} MB`
  if (bytes >= 1024) return `${Number((bytes / 1024).toFixed(2))} KB`
  return `${bytes} B`
}

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

function currentRange() {
  return {
    startAt: filters.start ? new Date(filters.start).toISOString() : undefined,
    endAt: filters.end ? new Date(filters.end).toISOString() : undefined,
  }
}

async function load(): Promise<void> {
  pageError.value = null
  try {
    await store.load(currentRange())
  } catch (error) {
    pageError.value = formatApiError(error)
  }
}

onMounted(load)
</script>

<template>
  <main class="admin-operations-page">
    <header class="admin-operations-heading">
      <div>
        <h2>运营概览</h2>
        <p>仅展示账号、数量、状态、稳定错误码和聚合质量数据。</p>
      </div>
      <div class="operations-filters" aria-label="运营时间范围">
        <label>开始时间<input v-model="filters.start" type="datetime-local"></label>
        <label>结束时间<input v-model="filters.end" type="datetime-local"></label>
        <el-button data-test="refresh-operations" type="primary" :loading="store.loading" @click="load">
          查询
        </el-button>
      </div>
    </header>

    <p v-if="pageError" class="admin-users-error" role="alert">{{ pageError }}</p>

    <section v-if="store.overview" class="workspace-card operations-section">
      <h3>系统总览</h3>
      <dl class="operations-metrics">
        <div><dt>账号</dt><dd>{{ store.overview.active_account_total }} / {{ store.overview.account_total }}</dd></div>
        <div><dt>知识库数量</dt><dd>{{ store.overview.knowledge_base_total }}</dd></div>
        <div><dt>文档数量</dt><dd>{{ store.overview.document_total }}</dd></div>
        <div><dt>有效存储</dt><dd>{{ formatBytes(store.overview.effective_document_bytes) }}</dd></div>
        <div><dt>Token</dt><dd>{{ store.overview.token_total.toLocaleString('zh-CN') }}</dd></div>
        <div><dt>估算费用</dt><dd>¥{{ store.overview.cost_total }}</dd></div>
        <div><dt>风险事件</dt><dd>{{ store.overview.risk_event_total }}</dd></div>
      </dl>
    </section>

    <section class="workspace-card operations-section">
      <h3>用户聚合</h3>
      <div class="operations-table-wrap">
        <table class="operations-table">
          <thead><tr><th>用户名</th><th>状态</th><th>知识库</th><th>文档</th><th>任务</th><th>Token</th><th>费用</th></tr></thead>
          <tbody>
            <tr v-for="user in store.users" :key="user.user_id">
              <td>{{ user.username }}</td>
              <td>{{ user.is_active ? '启用' : '停用' }}</td>
              <td>{{ user.knowledge_base_total }}</td>
              <td>{{ user.document_total }}</td>
              <td>{{ user.job_total }}</td>
              <td>{{ user.token_total.toLocaleString('zh-CN') }}</td>
              <td>¥{{ user.cost_total }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="store.users.length === 0" class="profile-empty">所选范围内暂无用户聚合。</p>
    </section>

    <section class="workspace-card operations-section">
      <h3>任务状态</h3>
      <div class="operations-table-wrap">
        <table class="operations-table">
          <thead><tr><th>资源类型</th><th>状态</th><th>阶段</th><th>尝试</th><th>错误码</th><th>创建时间</th></tr></thead>
          <tbody>
            <tr v-for="job in store.jobs.items" :key="job.id">
              <td>{{ job.resource_type }}</td><td>{{ job.status }}</td><td>{{ job.stage ?? '未知' }}</td>
              <td>{{ job.attempt_count }}</td><td>{{ job.error_code ?? '无' }}</td><td>{{ formatDate(job.created_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <el-button v-if="store.jobs.next_cursor" :loading="store.loadingMoreJobs" @click="store.loadMoreJobs">
        加载更多任务
      </el-button>
      <p v-if="store.jobs.items.length === 0" class="profile-empty">所选范围内暂无任务。</p>
    </section>

    <section class="workspace-card operations-section">
      <h3>质量聚合</h3>
      <dl v-if="store.quality" class="operations-metrics">
        <div v-for="(value, key) in store.quality.online_agent_metrics" :key="key">
          <dt>{{ metricLabels[key] ?? key }}</dt><dd>{{ value }}</dd>
        </div>
        <div><dt>有帮助反馈</dt><dd>{{ store.quality.feedback_distribution.helpful ?? 0 }}</dd></div>
        <div><dt>待改进反馈</dt><dd>{{ store.quality.feedback_distribution.unhelpful ?? 0 }}</dd></div>
        <div v-if="store.quality.latest_offline_evaluation">
          <dt>最新离线质量门</dt>
          <dd>{{ store.quality.latest_offline_evaluation.gate_passed ? '通过' : '未通过' }}</dd>
        </div>
      </dl>
      <p v-else class="profile-empty">暂无质量聚合。</p>
    </section>
  </main>
</template>

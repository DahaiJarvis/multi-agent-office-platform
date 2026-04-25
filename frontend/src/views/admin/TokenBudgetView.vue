<template>
  <div class="token-page">
    <div class="page-header">
      <h2>Token 预算管理</h2>
    </div>

    <div class="search-bar">
      <input
        v-model="searchUserId"
        class="search-input"
        placeholder="输入用户ID查询"
        @keydown.enter="loadData"
      />
      <button class="btn-primary" @click="loadData">查询</button>
    </div>

    <div v-if="loading" class="loading-state">
      <div class="spinner" />
      <span>加载中...</span>
    </div>

    <template v-else-if="tokenData">
      <div class="budget-cards">
        <div class="budget-card">
          <div class="budget-header">
            <span class="budget-title">用户 Token 用量</span>
            <span class="budget-user">{{ searchUserId }}</span>
          </div>
          <div class="budget-body">
            <div class="budget-row">
              <span class="budget-label">总消耗</span>
              <span class="budget-value">{{ formatNumber(tokenData.total_tokens_used || tokenData.total_tokens || 0) }}</span>
            </div>
            <div class="budget-row">
              <span class="budget-label">会话数</span>
              <span class="budget-value">{{ tokenData.session_count || tokenData.total_sessions || 0 }}</span>
            </div>
            <div class="budget-row">
              <span class="budget-label">平均每会话</span>
              <span class="budget-value">{{ formatNumber(tokenData.avg_tokens_per_session || 0) }}</span>
            </div>
          </div>
        </div>

        <div class="budget-card">
          <div class="budget-header">
            <span class="budget-title">预算检查</span>
            <span class="budget-status" :class="budgetStatus.class">{{ budgetStatus.label }}</span>
          </div>
          <div class="budget-body">
            <div class="budget-row">
              <span class="budget-label">预算上限</span>
              <span class="budget-value">{{ formatNumber(budgetData.budget_limit || 0) }}</span>
            </div>
            <div class="budget-row">
              <span class="budget-label">已使用</span>
              <span class="budget-value">{{ formatNumber(budgetData.tokens_used || 0) }}</span>
            </div>
            <div class="budget-row">
              <span class="budget-label">使用率</span>
              <span class="budget-value" :class="usageClass">{{ usagePercent }}%</span>
            </div>
            <div class="progress-bar">
              <div class="progress-fill" :style="{ width: usagePercent + '%' }" :class="usageClass" />
            </div>
            <div v-if="budgetData.model_downgrade" class="downgrade-notice">
              <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
                <path d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" />
              </svg>
              <span>已触发模型降级: {{ budgetData.model_downgrade }}</span>
            </div>
          </div>
        </div>
      </div>

      <div v-if="sessionBreakdown.length > 0" class="breakdown-card">
        <h3>会话 Token 明细</h3>
        <div class="breakdown-list">
          <div v-for="item in sessionBreakdown" :key="item.session_id" class="breakdown-row">
            <span class="breakdown-id">{{ item.session_id?.substring(0, 16) }}...</span>
            <div class="breakdown-bar-track">
              <div class="breakdown-bar-fill" :style="{ width: item.percentage + '%' }" />
            </div>
            <span class="breakdown-tokens">{{ formatNumber(item.tokens) }}</span>
          </div>
        </div>
      </div>
    </template>

    <div v-else class="empty-state">
      <p>输入用户ID查询 Token 使用情况</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { adminApi } from '../../api/admin'

const searchUserId = ref('')
const loading = ref(false)
const tokenData = ref<any>(null)
const budgetData = ref<any>({})
const sessionBreakdown = ref<any[]>([])

const usagePercent = computed(() => {
  const limit = budgetData.value.budget_limit || 0
  const used = budgetData.value.tokens_used || 0
  if (limit === 0) return 0
  return Math.min(Math.round((used / limit) * 100), 100)
})

const usageClass = computed(() => {
  const p = usagePercent.value
  if (p >= 90) return 'danger'
  if (p >= 70) return 'warning'
  return 'normal'
})

const budgetStatus = computed(() => {
  const p = usagePercent.value
  if (p >= 100) return { label: '超限', class: 'danger' }
  if (p >= 90) return { label: '即将超限', class: 'warning' }
  if (p >= 70) return { label: '注意', class: 'caution' }
  return { label: '正常', class: 'healthy' }
})

function formatNumber(n: number): string {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K'
  return String(n)
}

async function loadData() {
  if (!searchUserId.value.trim()) return
  loading.value = true

  try {
    const [usageRes, budgetRes] = await Promise.allSettled([
      adminApi.tokenUsage(searchUserId.value),
      adminApi.tokenBudget(searchUserId.value),
    ])

    if (usageRes.status === 'fulfilled') {
      tokenData.value = usageRes.value.data
      if (usageRes.value.data.sessions) {
        const maxTokens = Math.max(...usageRes.value.data.sessions.map((s: any) => s.tokens || 0), 1)
        sessionBreakdown.value = usageRes.value.data.sessions.map((s: any) => ({
          ...s,
          percentage: ((s.tokens || 0) / maxTokens) * 100,
        }))
      }
    }

    if (budgetRes.status === 'fulfilled') {
      budgetData.value = budgetRes.value.data
    }
  } catch { /* ignore */ }
  finally {
    loading.value = false
  }
}
</script>

<style scoped>
.token-page {
  max-width: 900px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 20px;
}

.page-header h2 {
  font-size: 20px;
  font-weight: 700;
}

.search-bar {
  display: flex;
  gap: 10px;
  margin-bottom: 24px;
}

.search-input {
  flex: 1;
  max-width: 320px;
  padding: 10px 14px;
  border: 1.5px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: 14px;
  outline: none;
  transition: border-color var(--transition-fast);
}

.search-input:focus {
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px var(--color-primary-bg);
}

.btn-primary {
  padding: 10px 20px;
  border-radius: var(--radius-md);
  background: var(--color-primary);
  color: white;
  font-size: 14px;
  font-weight: 600;
  transition: background var(--transition-fast);
}

.btn-primary:hover {
  background: var(--color-primary-dark);
}

.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 60px;
  color: var(--color-text-secondary);
}

.spinner {
  width: 20px;
  height: 20px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

.empty-state {
  text-align: center;
  padding: 60px;
  color: var(--color-text-secondary);
}

.budget-cards {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
  margin-bottom: 20px;
}

.budget-card {
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  padding: 20px;
}

.budget-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.budget-title {
  font-size: 14px;
  font-weight: 700;
}

.budget-user {
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--color-text-secondary);
}

.budget-status {
  font-size: 12px;
  font-weight: 600;
  padding: 2px 10px;
  border-radius: var(--radius-full);
}

.budget-status.healthy {
  background: var(--color-success-bg);
  color: var(--color-success);
}

.budget-status.caution {
  background: var(--color-info-bg);
  color: var(--color-info);
}

.budget-status.warning {
  background: var(--color-warning-bg);
  color: var(--color-warning);
}

.budget-status.danger {
  background: var(--color-danger-bg);
  color: var(--color-danger);
}

.budget-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.budget-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.budget-label {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.budget-value {
  font-size: 16px;
  font-weight: 700;
  font-family: var(--font-mono);
}

.budget-value.warning { color: var(--color-warning); }
.budget-value.danger { color: var(--color-danger); }

.progress-bar {
  height: 8px;
  background: var(--color-bg);
  border-radius: var(--radius-full);
  overflow: hidden;
  margin-top: 4px;
}

.progress-fill {
  height: 100%;
  border-radius: var(--radius-full);
  transition: width 0.6s ease;
}

.progress-fill.normal { background: var(--color-primary); }
.progress-fill.warning { background: var(--color-warning); }
.progress-fill.danger { background: var(--color-danger); }

.downgrade-notice {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  background: var(--color-warning-bg);
  color: var(--color-warning);
  border-radius: var(--radius-md);
  font-size: 12px;
  font-weight: 500;
  margin-top: 4px;
}

.breakdown-card {
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  padding: 20px;
}

.breakdown-card h3 {
  font-size: 14px;
  font-weight: 700;
  margin-bottom: 16px;
}

.breakdown-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.breakdown-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.breakdown-id {
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--color-text-secondary);
  min-width: 140px;
}

.breakdown-bar-track {
  flex: 1;
  height: 6px;
  background: var(--color-bg);
  border-radius: var(--radius-full);
  overflow: hidden;
}

.breakdown-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--color-primary), var(--color-primary-light));
  border-radius: var(--radius-full);
}

.breakdown-tokens {
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--color-text-secondary);
  min-width: 60px;
  text-align: right;
}

@media (max-width: 640px) {
  .budget-cards {
    grid-template-columns: 1fr;
  }
}
</style>

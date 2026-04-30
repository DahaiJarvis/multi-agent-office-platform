<template>
  <div class="token-page">
    <div class="page-header">
      <h2>Token 预算管理</h2>
    </div>

    <div class="guide-banner" v-if="!guideDismissed">
      <div class="guide-banner-content">
        <span class="guide-icon">?</span>
        <span>Token 预算管理用于查看和管控用户的 Token 消耗，支持设置预算上限和降级策略，避免资源超支。</span>
        <button class="guide-link" @click="showGuideDialog = true">查看详细指南</button>
        <button class="guide-close" @click="guideDismissed = true">&times;</button>
      </div>
    </div>

    <div class="search-bar">
      <div class="search-hint">输入用户ID查询该用户的 Token 消耗和预算配置</div>
      <div class="search-row">
      <input
        v-model="searchUserId"
        class="search-input"
        placeholder="输入用户ID查询"
        @keydown.enter="loadData"
      />
      <button class="btn-primary" @click="loadData">查询</button>
      <button v-if="isAdmin" class="btn-outline" @click="showUserList = !showUserList">
        {{ showUserList ? '收起用户列表' : '查看用户列表' }}
      </button>
      </div>
    </div>

    <div v-if="isAdmin && showUserList && userList.length > 0" class="user-quick-list">
      <span class="quick-label">快速选择:</span>
      <button
        v-for="u in userList"
        :key="u.user_id"
        class="user-chip"
        :class="{ active: searchUserId === u.user_id }"
        @click="selectUser(u.user_id)"
      >
        {{ u.display_name || u.user_id }}
      </button>
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
          <div class="inline-hint">展示该用户的 Token 累计消耗和会话统计</div>
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
          <div class="inline-hint">预算上限和使用率，超过阈值将触发模型降级</div>
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
              <span class="downgrade-hint">当使用率超过阈值时，系统自动切换到低成本模型以控制费用</span>
            </div>
          </div>
        </div>
      </div>

      <div v-if="sessionBreakdown.length > 0" class="breakdown-card">
        <h3>会话 Token 明细</h3>
        <div class="inline-hint">各会话的 Token 消耗分布，条形越长表示消耗越多</div>
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

    <el-dialog v-model="showGuideDialog" title="Token 预算管理使用指南" width="640px">
      <div class="guide-content">
        <div class="guide-section">
          <h4>什么是 Token 预算？</h4>
          <p>Token 是 LLM 调用的计费单位。Token 预算管理帮助管理员监控和管控每个用户的 Token 消耗，设置预算上限，防止资源超支。</p>
        </div>
        <div class="guide-section">
          <h4>使用流程</h4>
          <div class="flow-steps">
            <div class="flow-step">
              <div class="step-num">1</div>
              <div class="step-content">
                <strong>查询用户</strong>
                <p>输入用户ID，点击查询按钮查看该用户的 Token 使用情况</p>
              </div>
            </div>
            <div class="flow-step">
              <div class="step-num">2</div>
              <div class="step-content">
                <strong>查看用量</strong>
                <p>在"用户 Token 用量"卡片查看总消耗、会话数和平均消耗</p>
              </div>
            </div>
            <div class="flow-step">
              <div class="step-num">3</div>
              <div class="step-content">
                <strong>检查预算</strong>
                <p>在"预算检查"卡片查看预算上限、使用率和进度条</p>
              </div>
            </div>
            <div class="flow-step">
              <div class="step-num">4</div>
              <div class="step-content">
                <strong>关注降级</strong>
                <p>如果出现降级提示，说明已自动切换到低成本模型</p>
              </div>
            </div>
          </div>
        </div>
        <div class="guide-section">
          <h4>指标说明</h4>
          <div class="config-list">
            <div class="config-item"><code>总消耗</code> - 用户累计消耗的 Token 数量</div>
            <div class="config-item"><code>会话数</code> - 用户发起的对话会话总数</div>
            <div class="config-item"><code>预算上限</code> - 为用户设置的 Token 消耗上限</div>
            <div class="config-item"><code>使用率</code> - 已消耗/预算上限的百分比，超过阈值触发降级</div>
            <div class="config-item"><code>模型降级</code> - 使用率超标后自动切换到低成本模型</div>
          </div>
        </div>
        <div class="guide-section">
          <h4>使用建议</h4>
          <p>定期检查高消耗用户的预算状态。当使用率接近阈值时，可考虑调整预算上限或优化 Agent 提示词以减少 Token 消耗。管理员可通过"查看用户列表"快速切换查看不同用户。</p>
        </div>
      </div>
      <template #footer>
        <el-button type="primary" @click="showGuideDialog = false">知道了</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { adminApi } from '../../api/admin'
import { useAuthStore } from '../../stores/auth'

const authStore = useAuthStore()
const isAdmin = computed(() => authStore.roles.includes('admin'))

const searchUserId = ref('')
const loading = ref(false)
const guideDismissed = ref(false)
const showGuideDialog = ref(false)
const tokenData = ref<any>(null)
const budgetData = ref<any>({})
const sessionBreakdown = ref<any[]>([])
const showUserList = ref(false)
const userList = ref<any[]>([])

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

function selectUser(userId: string) {
  searchUserId.value = userId
  loadData()
}

async function loadUserList() {
  if (!isAdmin.value) return
  try {
    const data = await adminApi.listUsers()
    userList.value = data?.items || []
  } catch {
    userList.value = []
  }
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
      const usageData = usageRes.value
      tokenData.value = usageData
      if (usageData.sessions) {
        const maxTokens = Math.max(...usageData.sessions.map((s: any) => s.tokens || 0), 1)
        sessionBreakdown.value = usageData.sessions.map((s: any) => ({
          ...s,
          percentage: ((s.tokens || 0) / maxTokens) * 100,
        }))
      }
    }

    if (budgetRes.status === 'fulfilled') {
      budgetData.value = budgetRes.value
    }
  } catch {
    ElMessage.error('加载 Token 用量数据失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  if (authStore.userId) {
    searchUserId.value = authStore.userId
    loadData()
  }
  if (isAdmin.value) {
    loadUserList()
  }
})
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
  margin-bottom: 12px;
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

.btn-outline {
  padding: 10px 16px;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-primary);
  color: var(--color-primary);
  font-size: 13px;
  font-weight: 500;
  transition: all var(--transition-fast);
  white-space: nowrap;
}

.btn-outline:hover {
  background: rgba(99,102,241,0.06);
}

.user-quick-list {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 20px;
  padding: 12px 16px;
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
}

.quick-label {
  font-size: 12px;
  color: var(--color-text-tertiary);
  font-weight: 500;
}

.user-chip {
  padding: 4px 12px;
  border-radius: var(--radius-full);
  font-size: 12px;
  font-weight: 500;
  background: var(--color-bg);
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border-light);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.user-chip:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.user-chip.active {
  background: var(--color-primary);
  color: white;
  border-color: var(--color-primary);
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
.guide-btn { margin-left: auto; }
.guide-banner { background: rgba(99,102,241,0.06); border: 1px solid rgba(99,102,241,0.15); border-radius: var(--radius-lg); padding: 12px 16px; margin-bottom: 16px; }
.guide-banner-content { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--color-text-secondary); line-height: 1.5; }
.guide-icon { width: 22px; height: 22px; border-radius: 50%; background: var(--color-primary); color: white; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; flex-shrink: 0; }
.guide-link { color: var(--color-primary); font-weight: 500; cursor: pointer; white-space: nowrap; }
.guide-link:hover { text-decoration: underline; }
.guide-close { margin-left: auto; color: var(--color-text-tertiary); font-size: 18px; cursor: pointer; padding: 0 4px; line-height: 1; }
.guide-close:hover { color: var(--color-text); }
.search-hint { font-size: 12px; color: var(--color-text-tertiary); margin-bottom: 8px; }
.search-row { display: flex; gap: 10px; align-items: center; }
.inline-hint { font-size: 12px; color: var(--color-text-tertiary); margin-bottom: 8px; line-height: 1.5; }
.downgrade-hint { font-size: 11px; color: var(--color-text-tertiary); margin-left: 4px; }
.guide-content { max-height: 60vh; overflow-y: auto; }
.guide-section { margin-bottom: 20px; }
.guide-section h4 { font-size: 15px; font-weight: 600; color: var(--color-text); margin: 0 0 8px; }
.guide-section p { font-size: 13px; color: var(--color-text-secondary); line-height: 1.6; margin: 0 0 8px; }
.guide-section code { background: rgba(99,102,241,0.08); color: var(--color-primary); padding: 1px 6px; border-radius: 4px; font-size: 12px; }
.flow-steps { display: flex; flex-direction: column; gap: 12px; margin-top: 8px; }
.flow-step { display: flex; gap: 12px; align-items: flex-start; }
.step-num { width: 24px; height: 24px; border-radius: 50%; background: var(--color-primary); color: white; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; flex-shrink: 0; }
.step-content strong { font-size: 13px; color: var(--color-text); }
.step-content p { font-size: 12px; color: var(--color-text-secondary); margin: 2px 0 0; }
.config-list { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
.config-item { font-size: 13px; color: var(--color-text-secondary); line-height: 1.6; }
.config-item code { background: rgba(99,102,241,0.08); color: var(--color-primary); padding: 1px 6px; border-radius: 4px; font-size: 12px; }
</style>

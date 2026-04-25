<template>
  <div class="dashboard-page">
    <div class="page-header">
      <h2>运营仪表盘</h2>
      <button class="btn-refresh" @click="loadData">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
          <path fill-rule="evenodd" d="M8 3a5 5 0 110 10A5 5 0 018 3zm0 1.5a3.5 3.5 0 100 7 3.5 3.5 0 000-7z" opacity="0.4" />
          <path d="M8 1a7 7 0 016.95 6.25h-1.5A5.5 5.5 0 008 2.5a5.5 5.5 0 00-5.45 4.75h-1.5A7 7 0 018 1z" />
        </svg>
        刷新
      </button>
    </div>

    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-icon" style="background: var(--color-primary-bg); color: var(--color-primary)">
          <svg width="22" height="22" viewBox="0 0 20 20" fill="currentColor">
            <path d="M2 5a2 2 0 012-2h8a2 2 0 012 2v6a2 2 0 01-2 2H6l-4 3V5z" />
          </svg>
        </div>
        <div class="stat-detail">
          <span class="stat-value">{{ metrics.total_sessions || 0 }}</span>
          <span class="stat-label">总会话数</span>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-icon" style="background: var(--color-success-bg); color: var(--color-success)">
          <svg width="22" height="22" viewBox="0 0 20 20" fill="currentColor">
            <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" />
          </svg>
        </div>
        <div class="stat-detail">
          <span class="stat-value">{{ metrics.active_sessions || 0 }}</span>
          <span class="stat-label">活跃会话</span>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-icon" style="background: var(--color-warning-bg); color: var(--color-warning)">
          <svg width="22" height="22" viewBox="0 0 20 20" fill="currentColor">
            <path d="M10 2a8 8 0 100 16 8 8 0 000-16zm1 11H9v-2h2v2zm0-4H9V5h2v4z" />
          </svg>
        </div>
        <div class="stat-detail">
          <span class="stat-value">{{ formatTokens(metrics.total_tokens_used || 0) }}</span>
          <span class="stat-label">Token 消耗</span>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-icon" style="background: var(--color-info-bg); color: var(--color-info)">
          <svg width="22" height="22" viewBox="0 0 20 20" fill="currentColor">
            <path d="M8.864.046C7.908-.193 7.02.53 6.956 1.466c-.072 1.051-.23 2.016-.428 2.59-.228.706-.822 1.504-1.724 2.194C4.284 4.457 3.502 4 2.5 4A2.5 2.5 0 000 6.5v3A2.5 2.5 0 002.5 12c.964 0 1.727-.43 2.252-.925a13.36 13.36 0 002.248.825V14.5a1.5 1.5 0 003 0v-2.292c.462-.16.903-.388 1.284-.654l.128-.09c.388-.275.896-.625 1.388-.927h.001c.49-.3.962-.543 1.399-.684.44-.142.768-.158.998-.058A1.5 1.5 0 0016 8.5v-3a1.5 1.5 0 00-1.5-1.5h-2.034c-.272 0-.514-.098-.712-.224-.2-.128-.35-.296-.447-.465A5.922 5.922 0 0110.5 1.5c0-.322-.046-.632-.14-.927a2.435 2.435 0 00-.422-.836 1.743 1.743 0 00-1.074-.69z" />
          </svg>
        </div>
        <div class="stat-detail">
          <span class="stat-value">{{ Math.round((metrics.avg_feedback_score || 0) * 100) }}%</span>
          <span class="stat-label">满意度</span>
        </div>
      </div>
    </div>

    <div class="dashboard-grid">
      <div class="dashboard-card">
        <h3>Agent 调用分布</h3>
        <div v-if="agentData.length === 0" class="empty-hint">暂无数据</div>
        <div v-else class="agent-bars">
          <div v-for="agent in agentData" :key="agent.name" class="agent-bar-row">
            <span class="agent-name">{{ agent.name }}</span>
            <div class="bar-track">
              <div class="bar-fill" :style="{ width: agent.percentage + '%' }" />
            </div>
            <span class="agent-count">{{ agent.count }}</span>
          </div>
        </div>
      </div>

      <div class="dashboard-card">
        <h3>MCP 服务状态</h3>
        <div v-if="mcpLoading" class="empty-hint"><div class="spinner-sm" /></div>
        <div v-else-if="mcpServices.length === 0" class="empty-hint">暂无数据</div>
        <div v-else class="service-list">
          <div v-for="svc in mcpServices" :key="svc.name" class="service-item">
            <div class="service-dot" :class="svc.status" />
            <span class="service-name">{{ svc.name }}</span>
            <span class="service-latency">{{ svc.latency || '-' }}ms</span>
          </div>
        </div>
      </div>

      <div class="dashboard-card">
        <h3>故障转移状态</h3>
        <div v-if="failoverLoading" class="empty-hint"><div class="spinner-sm" /></div>
        <div v-else class="failover-info">
          <div class="failover-row">
            <span class="fo-label">当前 AZ</span>
            <span class="fo-value">{{ failover.current_az || '-' }}</span>
          </div>
          <div class="failover-row">
            <span class="fo-label">健康状态</span>
            <span class="fo-value" :class="failover.healthy ? 'text-success' : 'text-danger'">
              {{ failover.healthy ? '正常' : '异常' }}
            </span>
          </div>
          <div class="failover-row">
            <span class="fo-label">上次切换</span>
            <span class="fo-value">{{ failover.last_failover || '-' }}</span>
          </div>
        </div>
      </div>

      <div class="dashboard-card">
        <h3>反馈统计</h3>
        <div v-if="feedbackLoading" class="empty-hint"><div class="spinner-sm" /></div>
        <div v-else class="feedback-stats">
          <div class="fb-row">
            <span class="fb-icon positive">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
                <path d="M8.864.046C7.908-.193 7.02.53 6.956 1.466c-.072 1.051-.23 2.016-.428 2.59-.228.706-.822 1.504-1.724 2.194C4.284 4.457 3.502 4 2.5 4A2.5 2.5 0 000 6.5v3A2.5 2.5 0 002.5 12c.964 0 1.727-.43 2.252-.925a13.36 13.36 0 002.248.825V14.5a1.5 1.5 0 003 0v-2.292c.462-.16.903-.388 1.284-.654l.128-.09c.388-.275.896-.625 1.388-.927h.001c.49-.3.962-.543 1.399-.684.44-.142.768-.158.998-.058A1.5 1.5 0 0016 8.5v-3a1.5 1.5 0 00-1.5-1.5h-2.034c-.272 0-.514-.098-.712-.224-.2-.128-.35-.296-.447-.465A5.922 5.922 0 0110.5 1.5c0-.322-.046-.632-.14-.927a2.435 2.435 0 00-.422-.836 1.743 1.743 0 00-1.074-.69z" />
              </svg>
            </span>
            <span class="fb-label">好评</span>
            <span class="fb-count">{{ feedbackStats.thumbs_up || 0 }}</span>
          </div>
          <div class="fb-row">
            <span class="fb-icon negative">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" style="transform: rotate(180deg)">
                <path d="M8.864.046C7.908-.193 7.02.53 6.956 1.466c-.072 1.051-.23 2.016-.428 2.59-.228.706-.822 1.504-1.724 2.194C4.284 4.457 3.502 4 2.5 4A2.5 2.5 0 000 6.5v3A2.5 2.5 0 002.5 12c.964 0 1.727-.43 2.252-.925a13.36 13.36 0 002.248.825V14.5a1.5 1.5 0 003 0v-2.292c.462-.16.903-.388 1.284-.654l.128-.09c.388-.275.896-.625 1.388-.927h.001c.49-.3.962-.543 1.399-.684.44-.142.768-.158.998-.058A1.5 1.5 0 0016 8.5v-3a1.5 1.5 0 00-1.5-1.5h-2.034c-.272 0-.514-.098-.712-.224-.2-.128-.35-.296-.447-.465A5.922 5.922 0 0110.5 1.5c0-.322-.046-.632-.14-.927a2.435 2.435 0 00-.422-.836 1.743 1.743 0 00-1.074-.69z" />
              </svg>
            </span>
            <span class="fb-label">差评</span>
            <span class="fb-count">{{ feedbackStats.thumbs_down || 0 }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { adminApi } from '../../api/admin'
import { agentApi } from '../../api/agent'

const metrics = reactive<any>({})
const agentData = ref<any[]>([])
const mcpServices = ref<any[]>([])
const mcpLoading = ref(false)
const failover = reactive<any>({})
const failoverLoading = ref(false)
const feedbackStats = reactive<any>({})
const feedbackLoading = ref(false)

function formatTokens(n: number): string {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K'
  return String(n)
}

async function loadData() {
  try {
    const { data } = await adminApi.metricsSummary()
    Object.assign(metrics, data)
    if (data.agent_distribution) {
      const maxCount = Math.max(...data.agent_distribution.map((a: any) => a.count), 1)
      agentData.value = data.agent_distribution.map((a: any) => ({
        ...a,
        percentage: (a.count / maxCount) * 100,
      }))
    }
  } catch { /* ignore */ }

  mcpLoading.value = true
  try {
    const { data } = await adminApi.mcpStatus()
    mcpServices.value = data.services || data || []
  } catch { /* ignore */ }
  finally { mcpLoading.value = false }

  failoverLoading.value = true
  try {
    const { data } = await adminApi.failoverStatus()
    Object.assign(failover, data)
  } catch { /* ignore */ }
  finally { failoverLoading.value = false }

  feedbackLoading.value = true
  try {
    const { data } = await agentApi.getFeedbackStats()
    Object.assign(feedbackStats, data)
  } catch { /* ignore */ }
  finally { feedbackLoading.value = false }
}

onMounted(loadData)
</script>

<style scoped>
.dashboard-page {
  max-width: 1100px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
}

.page-header h2 {
  font-size: 20px;
  font-weight: 700;
}

.btn-refresh {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  color: var(--color-text-secondary);
  font-size: 13px;
  font-weight: 500;
  transition: all var(--transition-fast);
}

.btn-refresh:hover {
  border-color: var(--color-primary-light);
  color: var(--color-primary);
  background: var(--color-primary-bg);
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}

.stat-card {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 20px;
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  transition: all var(--transition-fast);
}

.stat-card:hover {
  box-shadow: var(--shadow-md);
  transform: translateY(-2px);
}

.stat-icon {
  width: 48px;
  height: 48px;
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.stat-detail {
  display: flex;
  flex-direction: column;
}

.stat-value {
  font-size: 24px;
  font-weight: 800;
  color: var(--color-text);
  line-height: 1.2;
}

.stat-label {
  font-size: 13px;
  color: var(--color-text-secondary);
  margin-top: 2px;
}

.dashboard-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
}

.dashboard-card {
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  padding: 20px;
}

.dashboard-card h3 {
  font-size: 14px;
  font-weight: 700;
  color: var(--color-text);
  margin-bottom: 16px;
}

.empty-hint {
  text-align: center;
  padding: 20px;
  color: var(--color-text-tertiary);
  font-size: 13px;
}

.spinner-sm {
  width: 16px;
  height: 16px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
  margin: 0 auto;
}

.agent-bars {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.agent-bar-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.agent-name {
  font-size: 13px;
  font-weight: 500;
  min-width: 100px;
  color: var(--color-text);
}

.bar-track {
  flex: 1;
  height: 8px;
  background: var(--color-bg);
  border-radius: var(--radius-full);
  overflow: hidden;
}

.bar-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--color-primary), var(--color-primary-light));
  border-radius: var(--radius-full);
  transition: width 0.6s ease;
}

.agent-count {
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--color-text-secondary);
  min-width: 40px;
  text-align: right;
}

.service-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.service-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 0;
}

.service-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.service-dot.healthy, .service-dot.active, .service-dot.online {
  background: var(--color-success);
  box-shadow: 0 0 6px rgba(5, 150, 105, 0.3);
}

.service-dot.unhealthy, .service-dot.offline {
  background: var(--color-danger);
}

.service-dot.degraded {
  background: var(--color-warning);
}

.service-name {
  flex: 1;
  font-size: 13px;
  font-weight: 500;
}

.service-latency {
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--color-text-tertiary);
}

.failover-info {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.failover-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.fo-label {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.fo-value {
  font-size: 13px;
  font-weight: 600;
}

.text-success { color: var(--color-success); }
.text-danger { color: var(--color-danger); }

.feedback-stats {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.fb-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.fb-icon {
  width: 32px;
  height: 32px;
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
}

.fb-icon.positive {
  background: var(--color-success-bg);
  color: var(--color-success);
}

.fb-icon.negative {
  background: var(--color-danger-bg);
  color: var(--color-danger);
}

.fb-label {
  flex: 1;
  font-size: 13px;
  color: var(--color-text-secondary);
}

.fb-count {
  font-size: 18px;
  font-weight: 700;
  color: var(--color-text);
}

@media (max-width: 768px) {
  .stats-grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .dashboard-grid {
    grid-template-columns: 1fr;
  }
}
</style>

<template>
  <div class="dr-page">
    <div class="page-header">
      <h2>灾备监控</h2>
      <div class="header-actions">
        <button class="btn-action" @click="verifyIntegrity" :disabled="verifying">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
            <path fill-rule="evenodd" d="M8 1a7 7 0 100 14A7 7 0 008 1zm0 1.5a5.5 5.5 0 110 11 5.5 5.5 0 010-11zm3.354 4.146a.5.5 0 00-.708-.708L7 9.586 5.354 7.94a.5.5 0 10-.708.708l2 2a.5.5 0 00.708 0l4-4z" />
          </svg>
          {{ verifying ? '校验中...' : '数据完整性校验' }}
        </button>
        <button class="btn-refresh" @click="loadAll">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
            <path fill-rule="evenodd" d="M8 3a5 5 0 110 10A5 5 0 018 3zm0 1.5a3.5 3.5 0 100 7 3.5 3.5 0 000-7z" opacity="0.4" />
            <path d="M8 1a7 7 0 016.95 6.25h-1.5A5.5 5.5 0 008 2.5a5.5 5.5 0 00-5.45 4.75h-1.5A7 7 0 018 1z" />
          </svg>
          刷新
        </button>
      </div>
    </div>

    <div class="guide-banner" v-if="!guideDismissed">
      <div class="guide-banner-content">
        <span class="guide-icon">?</span>
        <span>灾备监控页面展示 RTO/RPO 核心指标、心跳检测状态、跨区域复制延迟和故障转移历史，帮助评估系统容灾能力。</span>
        <button class="guide-link" @click="showGuideDialog = true">查看详细指南</button>
        <button class="guide-close" @click="guideDismissed = true">&times;</button>
      </div>
    </div>

    <!-- RTO/RPO 合规总览 -->
    <div class="compliance-grid">
      <div class="compliance-card" :class="drMetrics.compliance?.rto_compliant ? 'compliant' : 'violated'">
        <div class="compliance-icon">
          <svg width="24" height="24" viewBox="0 0 20 20" fill="currentColor">
            <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm1-12a1 1 0 10-2 0v4a1 1 0 00.293.707l2.828 2.829a1 1 0 101.415-1.415L11 9.586V6z" />
          </svg>
        </div>
        <div class="compliance-info">
          <span class="compliance-label">RTO 恢复时间</span>
          <span class="compliance-value">{{ formatDuration(drMetrics.rto?.current_seconds) }}</span>
          <span class="compliance-target">目标: {{ formatDuration(drMetrics.rto?.target_seconds) }}</span>
        </div>
        <div class="compliance-badge" :class="drMetrics.compliance?.rto_compliant ? 'ok' : 'fail'">
          {{ drMetrics.compliance?.rto_compliant ? '达标' : '违规' }}
        </div>
      </div>

      <div class="compliance-card" :class="drMetrics.compliance?.rpo_compliant ? 'compliant' : 'violated'">
        <div class="compliance-icon">
          <svg width="24" height="24" viewBox="0 0 20 20" fill="currentColor">
            <path d="M2 6a2 2 0 012-2h5l2 2h5a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" />
          </svg>
        </div>
        <div class="compliance-info">
          <span class="compliance-label">RPO 数据丢失</span>
          <span class="compliance-value">{{ formatDuration(drMetrics.rpo?.current_seconds) }}</span>
          <span class="compliance-target">目标: {{ formatDuration(drMetrics.rpo?.target_seconds) }}</span>
        </div>
        <div class="compliance-badge" :class="drMetrics.compliance?.rpo_compliant ? 'ok' : 'fail'">
          {{ drMetrics.compliance?.rpo_compliant ? '达标' : '违规' }}
        </div>
      </div>

      <div class="compliance-card" :class="drMetrics.integrity?.verified ? 'compliant' : 'violated'">
        <div class="compliance-icon">
          <svg width="24" height="24" viewBox="0 0 20 20" fill="currentColor">
            <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" />
          </svg>
        </div>
        <div class="compliance-info">
          <span class="compliance-label">数据完整性</span>
          <span class="compliance-value">{{ drMetrics.integrity?.verified ? '已校验' : '未校验' }}</span>
          <span class="compliance-target">上次: {{ drMetrics.integrity?.last_check || '-' }}</span>
        </div>
        <div class="compliance-badge" :class="drMetrics.integrity?.verified ? 'ok' : 'warn'">
          {{ drMetrics.integrity?.verified ? '通过' : '待校验' }}
        </div>
      </div>
    </div>

    <!-- 详细指标 -->
    <div class="metrics-grid">
      <div class="metrics-card">
        <h3>RTO 详细指标</h3>
        <div class="inline-hint">恢复时间目标: 从故障发生到服务恢复的最大时间</div>
        <div class="metric-rows">
          <div class="metric-row">
            <span class="metric-label">当前 RTO</span>
            <span class="metric-value" :class="drMetrics.compliance?.rto_compliant ? 'text-success' : 'text-danger'">
              {{ formatDuration(drMetrics.rto?.current_seconds) }}
            </span>
          </div>
          <div class="metric-row">
            <span class="metric-label">目标 RTO</span>
            <span class="metric-value">{{ formatDuration(drMetrics.rto?.target_seconds) }}</span>
          </div>
          <div class="metric-row">
            <span class="metric-label">上次故障转移耗时</span>
            <span class="metric-value">{{ formatDuration(drMetrics.rto?.last_failover_duration) }}</span>
          </div>
          <div class="metric-row">
            <span class="metric-label">故障转移次数</span>
            <span class="metric-value">{{ drMetrics.rto?.failover_count ?? '-' }}</span>
          </div>
          <div class="metric-row">
            <span class="metric-label">恢复次数</span>
            <span class="metric-value">{{ drMetrics.rto?.recovery_count ?? '-' }}</span>
          </div>
          <div class="metric-row">
            <span class="metric-label">违规次数</span>
            <span class="metric-value" :class="(drMetrics.rto?.violations ?? 0) > 0 ? 'text-danger' : ''">
              {{ drMetrics.rto?.violations ?? 0 }}
            </span>
          </div>
        </div>
      </div>

      <div class="metrics-card">
        <h3>RPO 详细指标</h3>
        <div class="inline-hint">恢复点目标: 故障时允许的最大数据丢失量</div>
        <div class="metric-rows">
          <div class="metric-row">
            <span class="metric-label">当前 RPO</span>
            <span class="metric-value" :class="drMetrics.compliance?.rpo_compliant ? 'text-success' : 'text-danger'">
              {{ formatDuration(drMetrics.rpo?.current_seconds) }}
            </span>
          </div>
          <div class="metric-row">
            <span class="metric-label">目标 RPO</span>
            <span class="metric-value">{{ formatDuration(drMetrics.rpo?.target_seconds) }}</span>
          </div>
          <div class="metric-row">
            <span class="metric-label">当前复制延迟</span>
            <span class="metric-value">{{ formatMs(drMetrics.rpo?.current_replication_lag_ms) }}</span>
          </div>
          <div class="metric-row">
            <span class="metric-label">最大复制延迟</span>
            <span class="metric-value">{{ formatMs(drMetrics.rpo?.max_replication_lag_ms) }}</span>
          </div>
          <div class="metric-row">
            <span class="metric-label">数据丢失量</span>
            <span class="metric-value">{{ formatBytes(drMetrics.rpo?.data_loss_bytes) }}</span>
          </div>
          <div class="metric-row">
            <span class="metric-label">违规次数</span>
            <span class="metric-value" :class="(drMetrics.rpo?.violations ?? 0) > 0 ? 'text-danger' : ''">
              {{ drMetrics.rpo?.violations ?? 0 }}
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- 心跳监控 + 跨区域复制 -->
    <div class="status-grid">
      <div class="status-card">
        <h3>心跳监控</h3>
        <div class="inline-hint">各组件心跳检测状态，连续失败超过阈值触发故障转移</div>
        <div v-if="heartbeatLoading" class="empty-hint"><div class="spinner-sm" /></div>
        <div v-else-if="heartbeat.targets.length === 0" class="empty-hint">暂无数据</div>
        <div v-else class="heartbeat-list">
          <div v-for="target in heartbeat.targets" :key="target.component" class="heartbeat-item">
            <div class="hb-dot" :class="target.alive ? 'alive' : 'dead'" />
            <span class="hb-name">{{ target.component }}</span>
            <span class="hb-latency">{{ target.alive ? target.latency_ms + 'ms' : 'TIMEOUT' }}</span>
            <span class="hb-failures" v-if="target.consecutive_failures > 0">
              {{ target.consecutive_failures }}次失败
            </span>
          </div>
        </div>
      </div>

      <div class="status-card">
        <h3>跨区域复制</h3>
        <div class="inline-hint">各区域数据复制延迟，延迟越大 RPO 风险越高</div>
        <div v-if="replicationLoading" class="empty-hint"><div class="spinner-sm" /></div>
        <div v-else-if="Object.keys(replication.regions).length === 0" class="empty-hint">暂无数据</div>
        <div v-else>
          <div class="replication-summary">
            <div class="rep-stat">
              <span class="rep-stat-value">{{ replication.healthy_regions }}/{{ replication.total_regions }}</span>
              <span class="rep-stat-label">健康区域</span>
            </div>
            <div class="rep-stat">
              <span class="rep-stat-value">{{ formatMs(replication.max_replication_lag_ms) }}</span>
              <span class="rep-stat-label">最大延迟</span>
            </div>
            <div class="rep-stat">
              <span class="rep-stat-value">{{ replication.estimated_rpo_seconds.toFixed(3) }}s</span>
              <span class="rep-stat-label">估算 RPO</span>
            </div>
          </div>
          <div class="region-list">
            <div v-for="(region, id) in replication.regions" :key="id" class="region-item">
              <div class="region-dot" :class="region.replication_lag_ms > 5000 ? 'unhealthy' : 'healthy'" />
              <span class="region-id">{{ id }}</span>
              <span class="region-name">{{ region.name }}</span>
              <span class="region-role">{{ region.role }}</span>
              <span class="region-lag" :class="region.replication_lag_ms > 5000 ? 'text-danger' : ''">
                {{ formatMs(region.replication_lag_ms) }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 故障转移历史 -->
    <div class="history-card">
      <h3>故障转移历史</h3>
      <div class="inline-hint">最近的故障转移事件记录</div>
      <div v-if="historyLoading" class="empty-hint"><div class="spinner-sm" /></div>
      <div v-else-if="failoverEvents.length === 0" class="empty-hint">暂无故障转移记录</div>
      <div v-else class="history-table-wrap">
        <table class="history-table">
          <thead>
            <tr>
              <th>组件</th>
              <th>源实例</th>
              <th>目标实例</th>
              <th>原因</th>
              <th>耗时</th>
              <th>状态</th>
              <th>时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="evt in failoverEvents" :key="evt.id">
              <td>{{ evt.component }}</td>
              <td class="mono">{{ evt.from_instance }}</td>
              <td class="mono">{{ evt.to_instance }}</td>
              <td>{{ evt.reason }}</td>
              <td class="mono">{{ evt.duration_seconds ? evt.duration_seconds.toFixed(2) + 's' : '-' }}</td>
              <td>
                <span class="status-tag" :class="evt.status === 'completed' ? 'success' : evt.status === 'failed' ? 'fail' : 'pending'">
                  {{ evt.status === 'completed' ? '完成' : evt.status === 'failed' ? '失败' : '进行中' }}
                </span>
              </td>
              <td class="mono">{{ formatTime(evt.started_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <el-dialog v-model="showGuideDialog" title="灾备监控使用指南" width="640px">
      <div class="guide-content">
        <div class="guide-section">
          <h4>什么是 RTO/RPO</h4>
          <p>RTO (Recovery Time Objective) 是从故障发生到服务恢复的最大允许时间；RPO (Recovery Point Objective) 是故障时允许的最大数据丢失量。两者是衡量系统容灾能力的核心指标。</p>
        </div>
        <div class="guide-section">
          <h4>指标说明</h4>
          <div class="config-list">
            <div class="config-item"><code>RTO</code> - 实际恢复时间 vs 目标恢复时间，达标表示系统在可接受时间内恢复</div>
            <div class="config-item"><code>RPO</code> - 实际数据丢失窗口 vs 目标数据丢失窗口，达标表示数据丢失在可控范围</div>
            <div class="config-item"><code>数据完整性</code> - 故障转移后主从数据一致性校验结果</div>
          </div>
        </div>
        <div class="guide-section">
          <h4>降低 RTO/RPO 的措施</h4>
          <div class="config-list">
            <div class="config-item"><code>心跳检测</code> - 2 秒间隔主动探测，4 秒内发现故障</div>
            <div class="config-item"><code>快速故障转移</code> - 连续 2 次失败即触发转移，超时 10 秒</div>
            <div class="config-item"><code>数据复制监控</code> - 实时监控跨区域复制延迟，延迟超过 5 秒告警</div>
            <div class="config-item"><code>完整性校验</code> - 故障转移后自动校验 Redis/PostgreSQL 主从一致性</div>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button type="primary" @click="showGuideDialog = false">知道了</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { adminApi } from '../../api/admin'
import type { DRStatus, FailoverEvent, ReplicationSummary, HeartbeatStatus } from '../../api/admin'

const drMetrics = reactive<DRStatus>({
  rto: { current_seconds: 0, target_seconds: 30, violations: 0, last_failover_duration: 0, failover_count: 0, recovery_count: 0 },
  rpo: { current_seconds: 0, target_seconds: 10, violations: 0, current_replication_lag_ms: 0, max_replication_lag_ms: 0, data_loss_bytes: 0 },
  integrity: { verified: false, last_check: '' },
  compliance: { rto_compliant: true, rpo_compliant: true },
})

const heartbeat = reactive<HeartbeatStatus>({ targets: [], total: 0, alive: 0, dead: 0 })
const replication = reactive<ReplicationSummary>({ regions: {}, max_replication_lag_ms: 0, estimated_rpo_seconds: 0, unhealthy_regions: [], total_regions: 0, healthy_regions: 0 })
const failoverEvents = ref<FailoverEvent[]>([])

const heartbeatLoading = ref(false)
const replicationLoading = ref(false)
const historyLoading = ref(false)
const verifying = ref(false)
const guideDismissed = ref(false)
const showGuideDialog = ref(false)

let refreshTimer: ReturnType<typeof setInterval> | null = null

function formatDuration(seconds: number | undefined): string {
  if (seconds === undefined || seconds === null) return '-'
  if (seconds < 1) return (seconds * 1000).toFixed(0) + 'ms'
  return seconds.toFixed(2) + 's'
}

function formatMs(ms: number | undefined): string {
  if (ms === undefined || ms === null) return '-'
  if (ms < 1000) return ms.toFixed(0) + 'ms'
  return (ms / 1000).toFixed(2) + 's'
}

function formatBytes(bytes: number | undefined): string {
  if (!bytes) return '0B'
  if (bytes < 1024) return bytes + 'B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + 'KB'
  return (bytes / 1024 / 1024).toFixed(1) + 'MB'
}

function formatTime(iso: string | undefined): string {
  if (!iso) return '-'
  try {
    return new Date(iso).toLocaleString('zh-CN')
  } catch {
    return iso
  }
}

async function loadDRMetrics() {
  try {
    const data = await adminApi.drMetrics()
    Object.assign(drMetrics, data)
  } catch {
    ElMessage.error('加载灾备指标失败')
  }
}

async function loadHeartbeat() {
  heartbeatLoading.value = true
  try {
    const data = await adminApi.heartbeatStatus()
    Object.assign(heartbeat, data)
  } catch {
    ElMessage.error('加载心跳状态失败')
  } finally {
    heartbeatLoading.value = false
  }
}

async function loadReplication() {
  replicationLoading.value = true
  try {
    const data = await adminApi.drReplication()
    Object.assign(replication, data)
  } catch {
    ElMessage.error('加载复制状态失败')
  } finally {
    replicationLoading.value = false
  }
}

async function loadHistory() {
  historyLoading.value = true
  try {
    const data = await adminApi.drHistory(20)
    failoverEvents.value = data.events || []
  } catch {
    ElMessage.error('加载故障转移历史失败')
  } finally {
    historyLoading.value = false
  }
}

async function verifyIntegrity() {
  verifying.value = true
  try {
    const result = await adminApi.drVerifyIntegrity()
    if (result.integrity_verified) {
      ElMessage.success('数据完整性校验通过')
    } else {
      ElMessage.warning('数据完整性校验未通过，请检查主从数据一致性')
    }
    await loadDRMetrics()
  } catch {
    ElMessage.error('完整性校验请求失败')
  } finally {
    verifying.value = false
  }
}

function loadAll() {
  loadDRMetrics()
  loadHeartbeat()
  loadReplication()
  loadHistory()
}

onMounted(() => {
  loadAll()
  refreshTimer = setInterval(loadAll, 15000)
})

onUnmounted(() => {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
})
</script>

<style scoped>
.dr-page {
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

.header-actions {
  display: flex;
  gap: 10px;
}

.btn-refresh,
.btn-action {
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
  cursor: pointer;
}

.btn-refresh:hover,
.btn-action:hover {
  border-color: var(--color-primary-light);
  color: var(--color-primary);
  background: var(--color-primary-bg);
}

.btn-action:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.guide-banner {
  background: rgba(99, 102, 241, 0.06);
  border: 1px solid rgba(99, 102, 241, 0.15);
  border-radius: var(--radius-lg);
  padding: 12px 16px;
  margin-bottom: 16px;
}

.guide-banner-content {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  color: var(--color-text-secondary);
  line-height: 1.5;
}

.guide-icon {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--color-primary);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
}

.guide-link {
  color: var(--color-primary);
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
}

.guide-link:hover {
  text-decoration: underline;
}

.guide-close {
  margin-left: auto;
  color: var(--color-text-tertiary);
  font-size: 18px;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
}

.guide-close:hover {
  color: var(--color-text);
}

.inline-hint {
  font-size: 12px;
  color: var(--color-text-tertiary);
  margin-bottom: 12px;
  line-height: 1.5;
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

/* 合规总览 */
.compliance-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}

.compliance-card {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 20px;
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  border-left: 4px solid;
  transition: all var(--transition-fast);
}

.compliance-card.compliant {
  border-left-color: var(--color-success);
}

.compliance-card.violated {
  border-left-color: var(--color-danger);
}

.compliance-icon {
  width: 48px;
  height: 48px;
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.compliance-card.compliant .compliance-icon {
  background: var(--color-success-bg);
  color: var(--color-success);
}

.compliance-card.violated .compliance-icon {
  background: var(--color-danger-bg);
  color: var(--color-danger);
}

.compliance-info {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.compliance-label {
  font-size: 12px;
  color: var(--color-text-secondary);
  margin-bottom: 2px;
}

.compliance-value {
  font-size: 22px;
  font-weight: 800;
  color: var(--color-text);
  line-height: 1.2;
}

.compliance-target {
  font-size: 11px;
  color: var(--color-text-tertiary);
  margin-top: 2px;
}

.compliance-badge {
  padding: 4px 10px;
  border-radius: var(--radius-full);
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
}

.compliance-badge.ok {
  background: var(--color-success-bg);
  color: var(--color-success);
}

.compliance-badge.fail {
  background: var(--color-danger-bg);
  color: var(--color-danger);
}

.compliance-badge.warn {
  background: var(--color-warning-bg);
  color: var(--color-warning);
}

/* 详细指标 */
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}

.metrics-card {
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  padding: 20px;
}

.metrics-card h3 {
  font-size: 14px;
  font-weight: 700;
  color: var(--color-text);
  margin-bottom: 4px;
}

.metric-rows {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.metric-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0;
  border-bottom: 1px solid var(--color-border-light);
}

.metric-row:last-child {
  border-bottom: none;
}

.metric-label {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.metric-value {
  font-size: 14px;
  font-weight: 600;
  font-family: var(--font-mono);
}

.text-success {
  color: var(--color-success);
}

.text-danger {
  color: var(--color-danger);
}

/* 心跳 + 复制 */
.status-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}

.status-card {
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  padding: 20px;
}

.status-card h3 {
  font-size: 14px;
  font-weight: 700;
  color: var(--color-text);
  margin-bottom: 4px;
}

.heartbeat-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.heartbeat-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 0;
}

.hb-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.hb-dot.alive {
  background: var(--color-success);
  box-shadow: 0 0 6px rgba(5, 150, 105, 0.3);
}

.hb-dot.dead {
  background: var(--color-danger);
  box-shadow: 0 0 6px rgba(220, 38, 38, 0.3);
}

.hb-name {
  flex: 1;
  font-size: 13px;
  font-weight: 500;
}

.hb-latency {
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--color-text-tertiary);
}

.hb-failures {
  font-size: 11px;
  color: var(--color-danger);
  background: var(--color-danger-bg);
  padding: 2px 6px;
  border-radius: var(--radius-sm);
}

.replication-summary {
  display: flex;
  gap: 20px;
  margin-bottom: 16px;
  padding: 12px;
  background: var(--color-bg);
  border-radius: var(--radius-md);
}

.rep-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.rep-stat-value {
  font-size: 18px;
  font-weight: 700;
  color: var(--color-text);
  font-family: var(--font-mono);
}

.rep-stat-label {
  font-size: 11px;
  color: var(--color-text-tertiary);
  margin-top: 2px;
}

.region-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.region-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 0;
}

.region-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.region-dot.healthy {
  background: var(--color-success);
}

.region-dot.unhealthy {
  background: var(--color-danger);
}

.region-id {
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--color-text-secondary);
  min-width: 80px;
}

.region-name {
  flex: 1;
  font-size: 13px;
  font-weight: 500;
}

.region-role {
  font-size: 11px;
  color: var(--color-text-tertiary);
  padding: 2px 8px;
  background: var(--color-bg);
  border-radius: var(--radius-sm);
}

.region-lag {
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--color-text-secondary);
  min-width: 60px;
  text-align: right;
}

/* 故障转移历史 */
.history-card {
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  padding: 20px;
  margin-bottom: 24px;
}

.history-card h3 {
  font-size: 14px;
  font-weight: 700;
  color: var(--color-text);
  margin-bottom: 4px;
}

.history-table-wrap {
  overflow-x: auto;
}

.history-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.history-table th {
  text-align: left;
  padding: 10px 12px;
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-secondary);
  border-bottom: 1px solid var(--color-border-light);
  white-space: nowrap;
}

.history-table td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--color-border-light);
  color: var(--color-text);
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-table .mono {
  font-family: var(--font-mono);
  font-size: 12px;
}

.status-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: var(--radius-full);
  font-size: 11px;
  font-weight: 600;
}

.status-tag.success {
  background: var(--color-success-bg);
  color: var(--color-success);
}

.status-tag.fail {
  background: var(--color-danger-bg);
  color: var(--color-danger);
}

.status-tag.pending {
  background: var(--color-warning-bg);
  color: var(--color-warning);
}

@media (max-width: 768px) {
  .compliance-grid {
    grid-template-columns: 1fr;
  }

  .metrics-grid,
  .status-grid {
    grid-template-columns: 1fr;
  }
}

.guide-content {
  max-height: 60vh;
  overflow-y: auto;
}

.guide-section {
  margin-bottom: 20px;
}

.guide-section h4 {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text);
  margin: 0 0 8px;
}

.guide-section p {
  font-size: 13px;
  color: var(--color-text-secondary);
  line-height: 1.6;
  margin: 0 0 8px;
}

.guide-section code {
  background: rgba(99, 102, 241, 0.08);
  color: var(--color-primary);
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 12px;
}

.config-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 8px;
}

.config-item {
  font-size: 13px;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.config-item code {
  background: rgba(99, 102, 241, 0.08);
  color: var(--color-primary);
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 12px;
}
</style>

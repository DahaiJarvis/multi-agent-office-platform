<template>
  <div class="health-page">
    <div class="page-header">
      <h2>健康检查</h2>
      <div class="header-actions">
        <button class="btn-refresh" @click="loadHealth">
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
        <span>健康检查页面展示系统各组件的实时运行状态，帮助快速发现和定位服务异常。</span>
        <button class="guide-link" @click="showGuideDialog = true">查看详细指南</button>
        <button class="guide-close" @click="guideDismissed = true">&times;</button>
      </div>
    </div>

    <div v-if="loading" class="loading-state">
      <div class="spinner" />
      <span>检查中...</span>
    </div>

    <template v-else>
      <div class="health-overview">
        <div class="overview-card" :class="overallStatus">
          <div class="overview-icon">
            <svg v-if="overallStatus === 'healthy'" width="32" height="32" viewBox="0 0 20 20" fill="currentColor">
              <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" />
            </svg>
            <svg v-else-if="overallStatus === 'degraded'" width="32" height="32" viewBox="0 0 20 20" fill="currentColor">
              <path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" />
            </svg>
            <svg v-else width="32" height="32" viewBox="0 0 20 20" fill="currentColor">
              <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.28 7.22a.75.75 0 00-1.06 1.06L8.94 10l-1.72 1.72a.75.75 0 101.06 1.06L10 11.06l1.72 1.72a.75.75 0 101.06-1.06L11.06 10l1.72-1.72a.75.75 0 00-1.06-1.06L10 8.94 8.28 7.22z" />
            </svg>
          </div>
          <div class="overview-info">
            <span class="overview-status">{{ statusLabel }}</span>
            <span class="overview-desc">{{ healthDetail.degradation_level || '所有服务运行正常' }}</span>
          </div>
        </div>
      </div>

      <div class="status-legend">
        <span class="legend-item"><span class="legend-dot healthy"></span>正常 - 服务运行良好</span>
        <span class="legend-item"><span class="legend-dot degraded"></span>降级 - 部分功能受限</span>
        <span class="legend-item"><span class="legend-dot unhealthy"></span>异常 - 服务不可用</span>
      </div>

      <div class="section-hint">以下为各子系统组件的健康检查详情，左侧色条标识状态：绿色=正常，红色=异常</div>

      <div class="components-grid">
        <div v-for="comp in components" :key="comp.name" class="comp-card" :class="comp.status">
          <div class="comp-header">
            <div class="comp-dot" />
            <span class="comp-name">{{ comp.name }}</span>
          </div>
          <div class="comp-detail">
            <div class="comp-row">
              <span>状态</span>
              <span class="comp-status-text">{{ comp.statusText }}</span>
            </div>
            <div v-if="comp.latency" class="comp-row">
              <span>延迟</span>
              <span class="comp-latency">{{ comp.latency }}ms</span>
            </div>
            <div v-if="comp.error" class="comp-row">
              <span>错误</span>
              <span class="comp-error">{{ comp.error }}</span>
            </div>
          </div>
        </div>
      </div>
    </template>

    <el-dialog v-model="showGuideDialog" title="健康检查使用指南" width="640px">
      <div class="guide-content">
        <div class="guide-section">
          <h4>什么是健康检查？</h4>
          <p>健康检查页面实时监控平台各组件的运行状态，包括数据库、缓存、消息队列、LLM 服务等。当某个组件出现异常时，系统会自动标记并触发告警。</p>
        </div>
        <div class="guide-section">
          <h4>状态等级</h4>
          <div class="config-list">
            <div class="config-item"><code>正常</code> - 所有组件运行良好，无异常</div>
            <div class="config-item"><code>降级</code> - 部分非核心组件异常，核心功能仍可用但可能受限</div>
            <div class="config-item"><code>异常</code> - 核心组件不可用，需要立即处理</div>
          </div>
        </div>
        <div class="guide-section">
          <h4>组件指标</h4>
          <div class="config-list">
            <div class="config-item"><code>状态</code> - 组件当前的健康状态（正常/异常）</div>
            <div class="config-item"><code>延迟</code> - 组件的响应延迟，单位毫秒(ms)</div>
            <div class="config-item"><code>错误</code> - 异常时的错误信息，帮助定位问题</div>
          </div>
        </div>
        <div class="guide-section">
          <h4>排查建议</h4>
          <p>当发现组件异常时，可查看错误信息初步定位原因。常见问题包括：数据库连接超时、LLM 服务配额耗尽、缓存服务不可达等。建议结合审计日志进一步排查。</p>
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
import { adminApi } from '../../api/admin'

const loading = ref(false)
const healthDetail = ref<any>({})
const components = ref<any[]>([])
const guideDismissed = ref(false)
const showGuideDialog = ref(false)

const overallStatus = computed(() => {
  const level = healthDetail.value.degradation_level
  if (!level || level === 'none') return 'healthy'
  if (level === 'partial') return 'degraded'
  return 'unhealthy'
})

const statusLabel = computed(() => {
  const s = overallStatus.value
  if (s === 'healthy') return '系统正常'
  if (s === 'degraded') return '部分降级'
  return '系统异常'
})

async function loadHealth() {
  loading.value = true
  try {
    const data = await adminApi.healthDetail()
    healthDetail.value = data
    const comps: any[] = []
    if (data.checks) {
      for (const [name, check] of Object.entries(data.checks)) {
        const c = check as any
        comps.push({
          name,
          status: c.healthy ? 'healthy' : 'unhealthy',
          statusText: c.healthy ? '正常' : '异常',
          latency: c.latency_ms,
          error: c.error,
        })
      }
    }
    components.value = comps
  } catch {
    healthDetail.value = { degradation_level: 'full' }
    components.value = []
  } finally {
    loading.value = false
  }
}

onMounted(loadHealth)
</script>

<style scoped>
.health-page {
  max-width: 900px;
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

.health-overview {
  margin-bottom: 24px;
}

.overview-card {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 24px;
  border-radius: var(--radius-lg);
  border: 1px solid;
}

.overview-card.healthy {
  background: var(--color-success-bg);
  border-color: rgba(5, 150, 105, 0.2);
  color: var(--color-success);
}

.overview-card.degraded {
  background: var(--color-warning-bg);
  border-color: rgba(217, 119, 6, 0.2);
  color: var(--color-warning);
}

.overview-card.unhealthy {
  background: var(--color-danger-bg);
  border-color: rgba(220, 38, 38, 0.2);
  color: var(--color-danger);
}

.overview-info {
  display: flex;
  flex-direction: column;
}

.overview-status {
  font-size: 18px;
  font-weight: 700;
}

.overview-desc {
  font-size: 13px;
  opacity: 0.7;
  margin-top: 2px;
}

.components-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}

.comp-card {
  padding: 16px 20px;
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  border-left: 3px solid;
}

.comp-card.healthy {
  border-left-color: var(--color-success);
}

.comp-card.unhealthy {
  border-left-color: var(--color-danger);
}

.comp-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.comp-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.comp-card.healthy .comp-dot {
  background: var(--color-success);
}

.comp-card.unhealthy .comp-dot {
  background: var(--color-danger);
}

.comp-name {
  font-size: 14px;
  font-weight: 600;
}

.comp-detail {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.comp-row {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.comp-status-text {
  font-weight: 600;
}

.comp-card.healthy .comp-status-text {
  color: var(--color-success);
}

.comp-card.unhealthy .comp-status-text {
  color: var(--color-danger);
}

.comp-latency {
  font-family: var(--font-mono);
}

.comp-error {
  color: var(--color-danger);
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 640px) {
  .components-grid {
    grid-template-columns: 1fr;
  }
}
.header-actions { display: flex; gap: 10px; }
.btn-outline { padding: 7px 14px; border-radius: var(--radius-md); border: 1px solid var(--color-primary); color: var(--color-primary); font-weight: 600; font-size: 13px; transition: all var(--transition-fast); }
.btn-outline:hover { background: rgba(99,102,241,0.06); }
.guide-banner { background: rgba(99,102,241,0.06); border: 1px solid rgba(99,102,241,0.15); border-radius: var(--radius-lg); padding: 12px 16px; margin-bottom: 16px; }
.guide-banner-content { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--color-text-secondary); line-height: 1.5; }
.guide-icon { width: 22px; height: 22px; border-radius: 50%; background: var(--color-primary); color: white; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; flex-shrink: 0; }
.guide-link { color: var(--color-primary); font-weight: 500; cursor: pointer; white-space: nowrap; }
.guide-link:hover { text-decoration: underline; }
.guide-close { margin-left: auto; color: var(--color-text-tertiary); font-size: 18px; cursor: pointer; padding: 0 4px; line-height: 1; }
.guide-close:hover { color: var(--color-text); }
.status-legend { display: flex; gap: 20px; margin-bottom: 16px; padding: 10px 16px; background: var(--color-bg-elevated); border-radius: var(--radius-md); border: 1px solid var(--color-border-light); }
.legend-item { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--color-text-secondary); }
.legend-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.legend-dot.healthy { background: var(--color-success); }
.legend-dot.degraded { background: var(--color-warning); }
.legend-dot.unhealthy { background: var(--color-danger); }
.section-hint { font-size: 12px; color: var(--color-text-tertiary); margin-bottom: 12px; }
.guide-content { max-height: 60vh; overflow-y: auto; }
.guide-section { margin-bottom: 20px; }
.guide-section h4 { font-size: 15px; font-weight: 600; color: var(--color-text); margin: 0 0 8px; }
.guide-section p { font-size: 13px; color: var(--color-text-secondary); line-height: 1.6; margin: 0 0 8px; }
.guide-section code { background: rgba(99,102,241,0.08); color: var(--color-primary); padding: 1px 6px; border-radius: 4px; font-size: 12px; }
.config-list { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
.config-item { font-size: 13px; color: var(--color-text-secondary); line-height: 1.6; }
.config-item code { background: rgba(99,102,241,0.08); color: var(--color-primary); padding: 1px 6px; border-radius: 4px; font-size: 12px; }
</style>

<template>
  <div class="health-page">
    <div class="page-header">
      <h2>健康检查</h2>
      <button class="btn-refresh" @click="loadHealth">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
          <path fill-rule="evenodd" d="M8 3a5 5 0 110 10A5 5 0 018 3zm0 1.5a3.5 3.5 0 100 7 3.5 3.5 0 000-7z" opacity="0.4" />
          <path d="M8 1a7 7 0 016.95 6.25h-1.5A5.5 5.5 0 008 2.5a5.5 5.5 0 00-5.45 4.75h-1.5A7 7 0 018 1z" />
        </svg>
        刷新
      </button>
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
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { adminApi } from '../../api/admin'

const loading = ref(false)
const healthDetail = ref<any>({})
const components = ref<any[]>([])

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
    const { data } = await adminApi.healthDetail()
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
</style>

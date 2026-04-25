<template>
  <div class="audit-page">
    <div class="page-header">
      <h2>审计日志</h2>
      <div class="header-actions">
        <button class="btn-outline" @click="flushAudit">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
            <path d="M8 2a.75.75 0 01.75.75v4.5h4.5a.75.75 0 010 1.5h-4.5v4.5a.75.75 0 01-1.5 0v-4.5h-4.5a.75.75 0 010-1.5h4.5v-4.5A.75.75 0 018 2z" />
          </svg>
          刷新缓冲区
        </button>
        <button class="btn-refresh" @click="loadLogs">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
            <path fill-rule="evenodd" d="M8 3a5 5 0 110 10A5 5 0 018 3zm0 1.5a3.5 3.5 0 100 7 3.5 3.5 0 000-7z" opacity="0.4" />
            <path d="M8 1a7 7 0 016.95 6.25h-1.5A5.5 5.5 0 008 2.5a5.5 5.5 0 00-5.45 4.75h-1.5A7 7 0 018 1z" />
          </svg>
          刷新
        </button>
      </div>
    </div>

    <div class="filter-bar">
      <select v-model="filters.event_type" class="filter-select" @change="loadLogs">
        <option value="">全部事件类型</option>
        <option value="auth">认证</option>
        <option value="agent_call">Agent调用</option>
        <option value="admin">管理操作</option>
        <option value="data_access">数据访问</option>
      </select>
      <input
        v-model="filters.user_id"
        class="filter-input"
        placeholder="用户ID"
        @keydown.enter="loadLogs"
      />
      <input
        v-model="filters.action"
        class="filter-input"
        placeholder="操作"
        @keydown.enter="loadLogs"
      />
    </div>

    <div v-if="loading" class="loading-state">
      <div class="spinner" />
      <span>加载中...</span>
    </div>

    <div v-else-if="logs.length === 0" class="empty-state">
      <p>暂无审计日志</p>
    </div>

    <div v-else class="audit-table-wrapper">
      <table class="audit-table">
        <thead>
          <tr>
            <th>时间</th>
            <th>事件类型</th>
            <th>用户</th>
            <th>操作</th>
            <th>详情</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(log, i) in logs" :key="i">
            <td class="td-time">{{ formatTime(log.timestamp || log.created_at) }}</td>
            <td>
              <span class="event-tag" :class="log.event_type">{{ log.event_type }}</span>
            </td>
            <td class="td-mono">{{ log.user_id || '-' }}</td>
            <td>{{ log.action || '-' }}</td>
            <td class="td-detail">{{ truncate(log.details || log.detail || '', 60) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="logs.length > 0" class="pagination">
      <button class="btn-page" :disabled="offset === 0" @click="prevPage">上一页</button>
      <span class="page-info">第 {{ offset / limit + 1 }} 页</span>
      <button class="btn-page" :disabled="logs.length < limit" @click="nextPage">下一页</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { adminApi } from '../../api/admin'

const loading = ref(false)
const logs = ref<any[]>([])
const offset = ref(0)
const limit = 20

const filters = reactive({
  event_type: '',
  user_id: '',
  action: '',
})

function formatTime(t: string) {
  if (!t) return '-'
  return new Date(t).toLocaleString('zh-CN')
}

function truncate(str: string, max: number) {
  if (!str) return '-'
  return str.length > max ? str.substring(0, max) + '...' : str
}

async function loadLogs() {
  loading.value = true
  try {
    const params: any = { limit, offset: offset.value }
    if (filters.event_type) params.event_type = filters.event_type
    if (filters.user_id) params.user_id = filters.user_id
    if (filters.action) params.action = filters.action

    const { data } = await adminApi.auditLogs(params)
    logs.value = data.logs || data || []
  } catch {
    logs.value = []
  } finally {
    loading.value = false
  }
}

async function flushAudit() {
  try {
    await adminApi.auditFlush()
    loadLogs()
  } catch { /* ignore */ }
}

function prevPage() {
  offset.value = Math.max(0, offset.value - limit)
  loadLogs()
}

function nextPage() {
  offset.value += limit
  loadLogs()
}

onMounted(loadLogs)
</script>

<style scoped>
.audit-page {
  max-width: 1000px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.page-header h2 {
  font-size: 20px;
  font-weight: 700;
}

.header-actions {
  display: flex;
  gap: 8px;
}

.btn-refresh, .btn-outline {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border-radius: var(--radius-md);
  font-size: 13px;
  font-weight: 500;
  transition: all var(--transition-fast);
}

.btn-refresh {
  border: 1px solid var(--color-border);
  color: var(--color-text-secondary);
}

.btn-refresh:hover {
  border-color: var(--color-primary-light);
  color: var(--color-primary);
  background: var(--color-primary-bg);
}

.btn-outline {
  border: 1px solid var(--color-primary);
  color: var(--color-primary);
}

.btn-outline:hover {
  background: var(--color-primary-bg);
}

.filter-bar {
  display: flex;
  gap: 10px;
  margin-bottom: 16px;
}

.filter-select, .filter-input {
  padding: 8px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--color-text);
  background: var(--color-bg-elevated);
  outline: none;
  transition: border-color var(--transition-fast);
}

.filter-select:focus, .filter-input:focus {
  border-color: var(--color-primary);
}

.filter-input {
  width: 140px;
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

.audit-table-wrapper {
  overflow-x: auto;
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border-light);
}

.audit-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.audit-table th {
  padding: 12px 16px;
  text-align: left;
  font-size: 11px;
  font-weight: 600;
  color: var(--color-text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  background: var(--color-bg);
  border-bottom: 1px solid var(--color-border-light);
}

.audit-table td {
  padding: 10px 16px;
  border-bottom: 1px solid var(--color-border-light);
  color: var(--color-text);
}

.audit-table tbody tr:hover {
  background: var(--color-bg);
}

.td-time {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--color-text-secondary);
  white-space: nowrap;
}

.td-mono {
  font-family: var(--font-mono);
  font-size: 12px;
}

.td-detail {
  font-size: 12px;
  color: var(--color-text-secondary);
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.event-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: var(--radius-full);
  font-size: 11px;
  font-weight: 600;
}

.event-tag.auth {
  background: var(--color-primary-bg);
  color: var(--color-primary);
}

.event-tag.agent_call {
  background: var(--color-success-bg);
  color: var(--color-success);
}

.event-tag.admin {
  background: var(--color-warning-bg);
  color: var(--color-warning);
}

.event-tag.data_access {
  background: var(--color-info-bg);
  color: var(--color-info);
}

.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  margin-top: 16px;
}

.btn-page {
  padding: 6px 14px;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  color: var(--color-text-secondary);
  font-size: 13px;
  transition: all var(--transition-fast);
}

.btn-page:hover:not(:disabled) {
  border-color: var(--color-primary-light);
  color: var(--color-primary);
}

.btn-page:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.page-info {
  font-size: 13px;
  color: var(--color-text-secondary);
}

@media (max-width: 640px) {
  .filter-bar {
    flex-wrap: wrap;
  }

  .filter-input {
    width: 100%;
  }
}
</style>

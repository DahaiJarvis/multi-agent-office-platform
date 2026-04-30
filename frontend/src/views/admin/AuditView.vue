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

    <div class="guide-banner" v-if="!guideDismissed">
      <div class="guide-banner-content">
        <span class="guide-icon">?</span>
        <span>审计日志记录平台所有关键操作，包括认证、Agent 调用、管理操作和数据访问，支持按条件筛选和导出。</span>
        <button class="guide-link" @click="showGuideDialog = true">查看详细指南</button>
        <button class="guide-close" @click="guideDismissed = true">&times;</button>
      </div>
    </div>

    <div class="filter-bar">
      <div class="filter-hint">通过以下条件筛选日志，缩小查找范围</div>
      <div class="filter-row">
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

    <el-dialog v-model="showGuideDialog" title="审计日志使用指南" width="640px">
      <div class="guide-content">
        <div class="guide-section">
          <h4>什么是审计日志？</h4>
          <p>审计日志记录平台所有关键操作的完整轨迹，包括谁在什么时间执行了什么操作。这是安全合规和问题排查的重要工具。</p>
        </div>
        <div class="guide-section">
          <h4>事件类型</h4>
          <div class="config-list">
            <div class="config-item"><code>auth</code> - 用户认证相关操作（登录、登出、Token 刷新等）</div>
            <div class="config-item"><code>agent_call</code> - Agent 调用记录（会话创建、消息发送等）</div>
            <div class="config-item"><code>admin</code> - 管理操作（配置变更、权限修改等）</div>
            <div class="config-item"><code>data_access</code> - 数据访问记录（知识库查询、文件下载等）</div>
          </div>
        </div>
        <div class="guide-section">
          <h4>筛选与搜索</h4>
          <p>支持按事件类型、用户ID、操作关键词筛选。输入筛选条件后按回车或点击刷新即可查询。点击"刷新缓冲区"可强制写入尚未持久化的日志。</p>
        </div>
        <div class="guide-section">
          <h4>排查建议</h4>
          <p>当发现异常操作时，可通过用户ID定位该用户的所有操作记录，结合时间线分析操作序列。管理操作类型的事件需重点关注，确保无未授权的配置变更。</p>
        </div>
      </div>
      <template #footer>
        <el-button type="primary" @click="showGuideDialog = false">知道了</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { adminApi } from '../../api/admin'
import { usePagination } from '../../composables/usePagination'

const logs = ref<any[]>([])
const guideDismissed = ref(false)
const showGuideDialog = ref(false)

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

async function fetchLogs(offset: number, limit: number) {
  try {
    const params: any = { limit, offset }
    if (filters.event_type) params.event_type = filters.event_type
    if (filters.user_id) params.user_id = filters.user_id
    if (filters.action) params.action = filters.action

    const data = await adminApi.auditLogs(params)
    logs.value = data.logs || data || []
  } catch {
    logs.value = []
  }
}

const { loading, offset, pageSize: limit, prevPage, nextPage } = usePagination({
  pageSize: 20,
  fetchFn: fetchLogs,
})

async function loadLogs() {
  loading.value = true
  await fetchLogs(offset.value, limit)
  loading.value = false
}

async function flushAudit() {
  try {
    await adminApi.auditFlush()
    loadLogs()
  } catch {
    ElMessage.error('刷新审计日志失败')
  }
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
.guide-banner { background: rgba(99,102,241,0.06); border: 1px solid rgba(99,102,241,0.15); border-radius: var(--radius-lg); padding: 12px 16px; margin-bottom: 16px; }
.guide-banner-content { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--color-text-secondary); line-height: 1.5; }
.guide-icon { width: 22px; height: 22px; border-radius: 50%; background: var(--color-primary); color: white; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; flex-shrink: 0; }
.guide-link { color: var(--color-primary); font-weight: 500; cursor: pointer; white-space: nowrap; }
.guide-link:hover { text-decoration: underline; }
.guide-close { margin-left: auto; color: var(--color-text-tertiary); font-size: 18px; cursor: pointer; padding: 0 4px; line-height: 1; }
.guide-close:hover { color: var(--color-text); }
.filter-hint { font-size: 12px; color: var(--color-text-tertiary); margin-bottom: 8px; }
.filter-row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.guide-content { max-height: 60vh; overflow-y: auto; }
.guide-section { margin-bottom: 20px; }
.guide-section h4 { font-size: 15px; font-weight: 600; color: var(--color-text); margin: 0 0 8px; }
.guide-section p { font-size: 13px; color: var(--color-text-secondary); line-height: 1.6; margin: 0 0 8px; }
.guide-section code { background: rgba(99,102,241,0.08); color: var(--color-primary); padding: 1px 6px; border-radius: 4px; font-size: 12px; }
.config-list { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
.config-item { font-size: 13px; color: var(--color-text-secondary); line-height: 1.6; }
.config-item code { background: rgba(99,102,241,0.08); color: var(--color-primary); padding: 1px 6px; border-radius: 4px; font-size: 12px; }
</style>

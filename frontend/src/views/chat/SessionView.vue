<template>
  <div class="session-page">
    <div class="page-header">
      <h2>会话管理</h2>
      <button class="btn-primary" @click="refreshSessions">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
          <path fill-rule="evenodd" d="M8 3a5 5 0 110 10A5 5 0 018 3zm0 1.5a3.5 3.5 0 100 7 3.5 3.5 0 000-7z" opacity="0.4" />
          <path d="M8 1a7 7 0 016.95 6.25h-1.5A5.5 5.5 0 008 2.5 5.5 5.5 0 002.55 7.25h-1.5A7 7 0 018 1z" />
        </svg>
        刷新
      </button>
    </div>

    <div v-if="loading" class="loading-state">
      <div class="spinner" />
      <span>加载中...</span>
    </div>

    <div v-else-if="sessions.length === 0" class="empty-state">
      <p>暂无会话记录</p>
    </div>

    <div v-else class="session-list">
      <div v-for="session in sessions" :key="session.session_id" class="session-card">
        <div class="session-info">
          <div class="session-id">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
              <path d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4z" />
            </svg>
            <span class="mono">{{ session.session_id }}</span>
          </div>
          <div class="session-meta">
            <span>消息数: {{ session.message_count }}</span>
            <span>渠道: {{ session.channel }}</span>
            <span>{{ formatTime(session.updated_at) }}</span>
          </div>
          <div v-if="session.active_agents?.length" class="session-agents">
            <span v-for="agent in session.active_agents" :key="agent" class="agent-tag">{{ agent }}</span>
          </div>
        </div>
        <div class="session-actions">
          <button class="btn-icon" title="查看历史" @click="viewHistory(session.session_id)">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
              <path d="M8 3.5a.5.5 0 00-1 0V8a.5.5 0 00.252.434l3.5 2a.5.5 0 00.496-.868L8 7.71V3.5z" />
              <path d="M8 16A8 8 0 108 0a8 8 0 000 16zm7-8A7 7 0 111 8a7 7 0 0114 0z" />
            </svg>
          </button>
          <button class="btn-icon" title="归档" @click="archiveSession(session.session_id)">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
              <path d="M0 2a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1v7.5a2.5 2.5 0 01-2.5 2.5h-9A2.5 2.5 0 011 12.5V5a1 1 0 01-1-1V2zm2 3v7.5A1.5 1.5 0 003.5 14h9a1.5 1.5 0 001.5-1.5V5H2zm13-3H1v2h14V2zM5 7.5a.5.5 0 01.5-.5h5a.5.5 0 010 1h-5a.5.5 0 01-.5-.5z" />
            </svg>
          </button>
          <button class="btn-icon danger" title="删除" @click="deleteSession(session.session_id)">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
              <path d="M5.5 5.5A.5.5 0 016 6v6a.5.5 0 01-1 0V6a.5.5 0 01.5-.5zm2.5 0a.5.5 0 01.5.5v6a.5.5 0 01-1 0V6a.5.5 0 01.5-.5zm3 .5a.5.5 0 00-1 0v6a.5.5 0 001 0V6z" />
              <path fill-rule="evenodd" d="M14.5 3a1 1 0 01-1 1H13v9a2 2 0 01-2 2H5a2 2 0 01-2-2V4h-.5a1 1 0 01-1-1V2a1 1 0 011-1H5.5l1-1h3l1 1h2.5a1 1 0 011 1v1zM4.118 4L4 4.059V13a1 1 0 001 1h6a1 1 0 001-1V4.059L11.882 4H4.118zM2.5 3V2h11v1h-11z" />
            </svg>
          </button>
        </div>
      </div>
    </div>

    <!-- 历史消息弹窗 -->
    <div v-if="showHistory" class="modal-overlay" @click.self="showHistory = false">
      <div class="modal-content">
        <div class="modal-header">
          <h3>会话历史</h3>
          <button class="btn-close" @click="showHistory = false">
            <svg width="18" height="18" viewBox="0 0 18 18" fill="currentColor">
              <path d="M4.293 4.293a1 1 0 011.414 0L9 7.586l3.293-3.293a1 1 0 111.414 1.414L10.414 9l3.293 3.293a1 1 0 01-1.414 1.414L9 10.414l-3.293 3.293a1 1 0 01-1.414-1.414L7.586 9 4.293 5.707a1 1 0 010-1.414z" />
            </svg>
          </button>
        </div>
        <div class="modal-body">
          <div v-if="historyLoading" class="loading-state"><div class="spinner" /></div>
          <div v-else-if="historyMessages.length === 0" class="empty-state"><p>暂无消息</p></div>
          <div v-else class="history-list">
            <div v-for="(msg, i) in historyMessages" :key="i" class="history-msg" :class="msg.role">
              <span class="history-role">{{ msg.role === 'user' ? '用户' : '助手' }}</span>
              <span class="history-content">{{ msg.content }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { sessionApi, type SessionInfo } from '../../api/session'

const authStore = useAuthStore()
const sessions = ref<SessionInfo[]>([])
const loading = ref(false)
const showHistory = ref(false)
const historyMessages = ref<any[]>([])
const historyLoading = ref(false)

function formatTime(t: string) {
  if (!t) return ''
  return new Date(t).toLocaleString('zh-CN')
}

async function refreshSessions() {
  loading.value = true
  try {
    const { data } = await sessionApi.listUserSessions(authStore.userId)
    sessions.value = data.sessions || data || []
  } catch {
    sessions.value = []
  } finally {
    loading.value = false
  }
}

async function viewHistory(sessionId: string) {
  showHistory.value = true
  historyLoading.value = true
  try {
    const { data } = await sessionApi.getHistory(sessionId)
    historyMessages.value = data.messages || []
  } catch {
    historyMessages.value = []
  } finally {
    historyLoading.value = false
  }
}

async function archiveSession(sessionId: string) {
  try {
    await sessionApi.archive(sessionId)
    refreshSessions()
  } catch { /* ignore */ }
}

async function deleteSession(sessionId: string) {
  if (!confirm('确认删除此会话?')) return
  try {
    await sessionApi.delete(sessionId)
    refreshSessions()
  } catch { /* ignore */ }
}

onMounted(refreshSessions)
</script>

<style scoped>
.session-page {
  max-width: 800px;
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

.btn-primary {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border-radius: var(--radius-md);
  background: var(--color-primary);
  color: white;
  font-size: 13px;
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
  padding: 40px;
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
  padding: 40px;
  color: var(--color-text-secondary);
}

.session-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.session-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  transition: all var(--transition-fast);
}

.session-card:hover {
  border-color: var(--color-border);
  box-shadow: var(--shadow-sm);
}

.session-info {
  flex: 1;
  min-width: 0;
}

.session-id {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--color-text);
  font-weight: 600;
  font-size: 14px;
  margin-bottom: 4px;
}

.mono {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--color-text-secondary);
}

.session-meta {
  display: flex;
  gap: 16px;
  font-size: 12px;
  color: var(--color-text-tertiary);
}

.session-agents {
  display: flex;
  gap: 4px;
  margin-top: 6px;
}

.agent-tag {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: var(--radius-full);
  background: var(--color-primary-bg);
  color: var(--color-primary);
  font-weight: 500;
}

.session-actions {
  display: flex;
  gap: 4px;
}

.btn-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: var(--radius-md);
  color: var(--color-text-tertiary);
  transition: all var(--transition-fast);
}

.btn-icon:hover {
  background: var(--color-bg);
  color: var(--color-text-secondary);
}

.btn-icon.danger:hover {
  background: var(--color-danger-bg);
  color: var(--color-danger);
}

.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
  animation: fadeIn 0.2s ease;
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

.modal-content {
  width: 90%;
  max-width: 600px;
  max-height: 70vh;
  background: var(--color-bg-elevated);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-xl);
  display: flex;
  flex-direction: column;
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 24px;
  border-bottom: 1px solid var(--color-border-light);
}

.modal-header h3 {
  font-size: 16px;
  font-weight: 700;
}

.btn-close {
  display: flex;
  color: var(--color-text-tertiary);
  transition: color var(--transition-fast);
}

.btn-close:hover {
  color: var(--color-text);
}

.modal-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px 24px;
}

.history-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.history-msg {
  display: flex;
  gap: 10px;
  padding: 8px 0;
}

.history-role {
  font-size: 12px;
  font-weight: 600;
  min-width: 40px;
  flex-shrink: 0;
}

.history-msg.user .history-role {
  color: var(--color-primary);
}

.history-msg.assistant .history-role {
  color: var(--color-success);
}

.history-content {
  font-size: 13px;
  color: var(--color-text);
  line-height: 1.5;
}
</style>

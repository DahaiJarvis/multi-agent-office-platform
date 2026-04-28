<template>
  <div class="chat-page">
    <aside class="history-panel">
      <div class="history-header">
        <span class="history-title">历史对话</span>
        <button class="btn-new-chat" @click="startNewChat" title="新对话">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
            <path d="M8 2a.75.75 0 01.75.75v4.5h4.5a.75.75 0 010 1.5h-4.5v4.5a.75.75 0 01-1.5 0v-4.5h-4.5a.75.75 0 010-1.5h4.5v-4.5A.75.75 0 018 2z" />
          </svg>
        </button>
      </div>
      <div class="history-list">
        <div v-if="historyLoading" class="history-loading">加载中...</div>
        <div v-else-if="sessionList.length === 0" class="history-empty">暂无对话记录</div>
        <div
          v-for="session in sessionList"
          :key="session.session_id"
          class="history-item"
          :class="{ active: chatStore.sessionId === session.session_id }"
          @click="loadSession(session.session_id)"
        >
          <div class="history-item-info">
            <div class="history-item-title">{{ getSessionTitle(session) }}</div>
            <div class="history-item-meta">
              <span>{{ session.message_count }}条消息</span>
              <span>{{ formatRelativeTime(session.updated_at) }}</span>
            </div>
          </div>
          <button
            class="history-item-delete"
            title="删除对话"
            @click.stop="deleteSession(session.session_id)"
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
              <path d="M5.5 5.5A.5.5 0 016 6v6a.5.5 0 01-1 0V6a.5.5 0 01.5-.5zm2.5 0a.5.5 0 01.5.5v6a.5.5 0 01-1 0V6a.5.5 0 01.5-.5zm3 .5a.5.5 0 00-1 0v6a.5.5 0 001 0V6z" />
              <path fill-rule="evenodd" d="M14.5 3a1 1 0 01-1 1H13v9a2 2 0 01-2 2H5a2 2 0 01-2-2V4h-.5a1 1 0 01-1-1V2a1 1 0 011-1H5.5l1-1h3l1 1h2.5a1 1 0 011 1v1zM4.118 4L4 4.059V13a1 1 0 001 1h6a1 1 0 001-1V4.059L11.882 4H4.118zM2.5 3V2h11v1h-11z" />
            </svg>
          </button>
        </div>
      </div>
    </aside>

    <div class="chat-main">
      <div class="chat-messages" ref="messagesContainer">
        <div v-if="chatStore.messages.length === 0" class="empty-state">
          <div class="empty-icon">
            <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
              <rect width="48" height="48" rx="12" fill="var(--color-primary-bg)" />
              <path d="M14 20a4 4 0 014-4h12a4 4 0 014 4v10a4 4 0 01-4 4H22l-8 5V20z" stroke="var(--color-primary)" stroke-width="2" stroke-linecap="round" />
              <circle cx="21" cy="24" r="1.5" fill="var(--color-primary)" />
              <circle cx="27" cy="24" r="1.5" fill="var(--color-primary)" />
              <circle cx="24" cy="24" r="1.5" fill="var(--color-primary)" />
            </svg>
          </div>
          <h3>开始一段新的对话</h3>
          <p>向 Agent 提问，获取智能办公协助</p>
          <div class="quick-actions">
            <button v-for="action in quickActions" :key="action.text" class="quick-btn" @click="sendQuickAction(action.text)">
              <span class="quick-icon">{{ action.icon }}</span>
              <span>{{ action.text }}</span>
            </button>
          </div>
        </div>

        <div
          v-for="msg in chatStore.messages"
          :key="msg.id"
          class="message-row"
          :class="[msg.role]"
        >
          <div v-if="msg.role === 'assistant'" class="msg-avatar assistant">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
              <path d="M8 1a7 7 0 100 14A7 7 0 008 1zM5.5 7a1 1 0 110-2 1 1 0 010 2zm5 0a1 1 0 110-2 1 1 0 010 2zm-5.13 2.84a.5.5 0 01.76-.66A3.5 3.5 0 008 10.5a3.5 3.5 0 002.87-1.32.5.5 0 01.76.66A4.5 4.5 0 018 11.5a4.5 4.5 0 01-3.63-1.66z" />
            </svg>
          </div>

          <div class="msg-content">
            <div class="msg-bubble" :class="[msg.role, { streaming: msg.streaming }]">
              <div class="msg-text" v-html="renderMarkdown(msg.content)" />
              <div v-if="msg.streaming" class="streaming-cursor">
                <span /><span /><span />
              </div>
            </div>

            <div v-if="msg.role === 'assistant' && !msg.streaming" class="msg-meta">
              <span v-if="msg.agentName" class="meta-agent">{{ msg.agentName }}</span>
              <span v-if="msg.collaborationMode" class="meta-mode">{{ msg.collaborationMode }}</span>
              <div class="msg-feedback">
                <button
                  class="feedback-btn"
                  :class="{ active: msg.feedback === 'thumbs_up' }"
                  @click="submitFeedback(msg.id, 'thumbs_up', msg)"
                  title="有帮助"
                >
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
                    <path d="M8.864.046C7.908-.193 7.02.53 6.956 1.466c-.072 1.051-.23 2.016-.428 2.59-.228.706-.822 1.504-1.724 2.194C4.284 4.457 3.502 4 2.5 4A2.5 2.5 0 000 6.5v3A2.5 2.5 0 002.5 12c.964 0 1.727-.43 2.252-.925a13.36 13.36 0 002.248.825V14.5a1.5 1.5 0 003 0v-2.292c.462-.16.903-.388 1.284-.654l.128-.09c.388-.275.896-.625 1.388-.927h.001c.49-.3.962-.543 1.399-.684.44-.142.768-.158.998-.058A1.5 1.5 0 0016 8.5v-3a1.5 1.5 0 00-1.5-1.5h-2.034c-.272 0-.514-.098-.712-.224-.2-.128-.35-.296-.447-.465A5.922 5.922 0 0110.5 1.5c0-.322-.046-.632-.14-.927a2.435 2.435 0 00-.422-.836 1.743 1.743 0 00-1.074-.69z" />
                  </svg>
                </button>
                <button
                  class="feedback-btn"
                  :class="{ active: msg.feedback === 'thumbs_down' }"
                  @click="submitFeedback(msg.id, 'thumbs_down', msg)"
                  title="需改进"
                >
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" style="transform: rotate(180deg)">
                    <path d="M8.864.046C7.908-.193 7.02.53 6.956 1.466c-.072 1.051-.23 2.016-.428 2.59-.228.706-.822 1.504-1.724 2.194C4.284 4.457 3.502 4 2.5 4A2.5 2.5 0 000 6.5v3A2.5 2.5 0 002.5 12c.964 0 1.727-.43 2.252-.925a13.36 13.36 0 002.248.825V14.5a1.5 1.5 0 003 0v-2.292c.462-.16.903-.388 1.284-.654l.128-.09c.388-.275.896-.625 1.388-.927h.001c.49-.3.962-.543 1.399-.684.44-.142.768-.158.998-.058A1.5 1.5 0 0016 8.5v-3a1.5 1.5 0 00-1.5-1.5h-2.034c-.272 0-.514-.098-.712-.224-.2-.128-.35-.296-.447-.465A5.922 5.922 0 0110.5 1.5c0-.322-.046-.632-.14-.927a2.435 2.435 0 00-.422-.836 1.743 1.743 0 00-1.074-.69z" />
                  </svg>
                </button>
              </div>
            </div>
          </div>

          <div v-if="msg.role === 'user'" class="msg-avatar user">
            {{ authStore.userId.charAt(0).toUpperCase() }}
          </div>
        </div>
      </div>

      <div class="chat-input-area">
        <div class="input-toolbar">
          <KbSelector @select="handleKbSelect" />
          <FileUploader @upload="handleFileUpload" />
        </div>
        <div class="input-wrapper">
          <textarea
            ref="inputRef"
            v-model="inputText"
            class="chat-textarea"
            placeholder="输入消息，按 Enter 发送..."
            rows="1"
            :disabled="chatStore.isStreaming"
            @keydown.enter.exact.prevent="handleSend"
            @input="autoResize"
          />
          <button
            class="btn-send"
            :disabled="!inputText.trim() || chatStore.isStreaming"
            @click="handleSend"
          >
            <svg width="18" height="18" viewBox="0 0 20 20" fill="currentColor">
              <path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 001.169 1.409l5-1.429A1 1 0 009 15.571V11a1 1 0 112 0v4.571a1 1 0 00.725.962l5 1.428a1 1 0 001.17-1.408l-7-14z" />
            </svg>
          </button>
        </div>
        <div class="input-footer">
          <span class="session-info" v-if="chatStore.sessionId">
            会话: {{ chatStore.sessionId.substring(0, 12) }}...
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../../stores/auth'
import { useChatStore, type Message } from '../../stores/chat'
import { agentApi } from '../../api/agent'
import { sessionApi, type SessionInfo } from '../../api/session'
import { knowledgeApi, type ParseResultItem } from '../../api/knowledge'
import KbSelector from '../../components/chat/KbSelector.vue'
import FileUploader from '../../components/chat/FileUploader.vue'

const authStore = useAuthStore()
const chatStore = useChatStore()

const inputText = ref('')
const inputRef = ref<HTMLTextAreaElement>()
const messagesContainer = ref<HTMLDivElement>()
const selectedKbId = ref('')
const sessionList = ref<SessionInfo[]>([])
const historyLoading = ref(false)

const quickActions = [
  { icon: '📋', text: '帮我查看今天的待办事项' },
  { icon: '📊', text: '查询本月销售数据' },
  { icon: '📅', text: '安排明天下午的会议' },
  { icon: '📧', text: '帮我起草一封项目周报邮件' },
]

function renderMarkdown(text: string): string {
  if (!text) return ''
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code class="lang-$1">$2</code></pre>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/\n/g, '<br>')
}

function autoResize() {
  const el = inputRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 160) + 'px'
}

function scrollToBottom() {
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
    }
  })
}

watch(() => chatStore.messages.length, scrollToBottom)
watch(() => chatStore.messages[chatStore.messages.length - 1]?.content, scrollToBottom)

function sendQuickAction(text: string) {
  inputText.value = text
  handleSend()
}

async function handleSend() {
  const text = inputText.value.trim()
  if (!text || chatStore.isStreaming) return

  inputText.value = ''
  if (inputRef.value) {
    inputRef.value.style.height = 'auto'
  }

  chatStore.addUserMessage(text)
  chatStore.isStreaming = true

  const streamingId = chatStore.addStreamingMessage()
  scrollToBottom()

  let agentName = ''
  let intent = ''
  let mode = ''

  agentApi.chatStreamFetch(
    {
      message: text,
      session_id: chatStore.sessionId || undefined,
      user_id: authStore.userId,
      knowledge_base_id: selectedKbId.value || undefined,
    },
    (data) => {
      if (data.event === 'session_id') {
        chatStore.setSessionId(data.data)
        refreshSessionList()
      } else if (data.event === 'intent') {
        try {
          const intentData = typeof data.data === 'string' ? JSON.parse(data.data) : data.data
          agentName = intentData.agent || ''
          intent = intentData.intent || ''
          mode = intentData.mode || ''
        } catch { /* skip */ }
      } else if (data.event === 'chunk') {
        chatStore.appendToStreamingMessage(streamingId, data.data)
        scrollToBottom()
      } else if (data.event === 'status' && data.data === 'completed') {
        chatStore.finalizeStreamingMessage(streamingId, { agentName, intent, mode })
      } else if (data.event === 'error') {
        chatStore.appendToStreamingMessage(streamingId, `\n\n[错误] ${data.message || '服务异常'}`)
        chatStore.finalizeStreamingMessage(streamingId)
        chatStore.isStreaming = false
      }
    },
    (error) => {
      chatStore.appendToStreamingMessage(streamingId, `\n\n[错误] ${error.message || '请求失败'}`)
      chatStore.finalizeStreamingMessage(streamingId)
      chatStore.isStreaming = false
    },
    () => {
      chatStore.finalizeStreamingMessage(streamingId, { agentName, intent, mode })
      chatStore.isStreaming = false
      refreshSessionList()
    },
  )
}

async function submitFeedback(messageId: string, type: 'thumbs_up' | 'thumbs_down', msg: Message) {
  const idx = chatStore.messages.indexOf(msg)
  if (idx === -1) return

  try {
    await agentApi.submitFeedback({
      session_id: chatStore.sessionId,
      message_index: idx,
      feedback_type: type,
      agent_name: msg.agentName,
      intent: msg.intent,
    })
    chatStore.setMessageFeedback(messageId, type)
  } catch { /* ignore */ }
}

function startNewChat() {
  chatStore.clearChat()
  inputRef.value?.focus()
}

async function refreshSessionList() {
  try {
    const { data } = await sessionApi.listUserSessions(authStore.userId, 50)
    sessionList.value = data.sessions || data || []
  } catch {
    sessionList.value = []
  }
}

async function loadSession(sessionId: string) {
  if (chatStore.isStreaming) return
  if (chatStore.sessionId === sessionId) return

  try {
    const { data } = await sessionApi.getHistory(sessionId)
    const messages = data.messages || []
    chatStore.clearChat()
    chatStore.setSessionId(sessionId)
    chatStore.loadHistory(messages)
    scrollToBottom()
  } catch {
    ElMessage.error('加载对话历史失败')
  }
}

async function deleteSession(sessionId: string) {
  try {
    await sessionApi.delete(sessionId)
    if (chatStore.sessionId === sessionId) {
      chatStore.clearChat()
    }
    refreshSessionList()
  } catch {
    ElMessage.error('删除对话失败')
  }
}

function getSessionTitle(session: SessionInfo): string {
  return `${session.message_count}条消息的对话`
}

function formatRelativeTime(t: string): string {
  if (!t) return ''
  const date = new Date(t)
  const now = new Date()
  const diff = now.getTime() - date.getTime()
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes}分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}小时前`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}天前`
  return date.toLocaleDateString('zh-CN')
}

function handleKbSelect(kbId: string) {
  selectedKbId.value = kbId
}

async function handleFileUpload(files: File[]) {
  if (files.length === 0) return

  try {
    const res = await knowledgeApi.parseFiles(files)
    const parsePayload = res.data?.data || res.data
    const parsedContent = parsePayload?.results?.map((r: ParseResultItem) => r.content || r.text || '').join('\n\n')
    if (parsedContent) {
      inputText.value = inputText.value
        ? `${inputText.value}\n\n[文件内容]\n${parsedContent}`
        : `[文件内容]\n${parsedContent}`
    }
  } catch (err) {
    ElMessage.error('文件解析失败')
  }
}

onMounted(() => {
  inputRef.value?.focus()
  refreshSessionList()
})
</script>

<style scoped>
.chat-page {
  display: flex;
  height: calc(100vh - var(--header-height) - 48px);
  max-width: 1100px;
  margin: 0 auto;
  width: 100%;
}

.history-panel {
  width: 240px;
  flex-shrink: 0;
  border-right: 1px solid var(--color-border-light);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.history-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--color-border-light);
}

.history-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--color-text);
}

.history-header .btn-new-chat {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-sm);
  color: var(--color-text-secondary);
  transition: all var(--transition-fast);
}

.history-header .btn-new-chat:hover {
  background: var(--color-primary-bg);
  color: var(--color-primary);
}

.history-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.history-loading,
.history-empty {
  text-align: center;
  padding: 24px 12px;
  font-size: 13px;
  color: var(--color-text-tertiary);
}

.history-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--transition-fast);
  margin-bottom: 2px;
}

.history-item:hover {
  background: var(--color-bg-elevated);
}

.history-item.active {
  background: var(--color-primary-bg);
}

.history-item-info {
  flex: 1;
  min-width: 0;
}

.history-item-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.history-item.active .history-item-title {
  color: var(--color-primary);
}

.history-item-meta {
  display: flex;
  gap: 8px;
  font-size: 11px;
  color: var(--color-text-tertiary);
  margin-top: 2px;
}

.history-item-delete {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: var(--radius-sm);
  color: var(--color-text-tertiary);
  opacity: 0;
  transition: all var(--transition-fast);
  flex-shrink: 0;
}

.history-item:hover .history-item-delete {
  opacity: 1;
}

.history-item-delete:hover {
  background: var(--color-danger-bg);
  color: var(--color-danger);
}

.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 0 16px;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px 0;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  text-align: center;
  padding: 40px 20px;
}

.empty-icon {
  margin-bottom: 20px;
}

.empty-state h3 {
  font-size: 20px;
  font-weight: 700;
  color: var(--color-text);
  margin-bottom: 6px;
}

.empty-state p {
  font-size: 14px;
  color: var(--color-text-secondary);
  margin-bottom: 28px;
}

.quick-actions {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
  max-width: 460px;
  width: 100%;
}

.quick-btn {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  border-radius: var(--radius-md);
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border);
  font-size: 13px;
  color: var(--color-text);
  text-align: left;
  transition: all var(--transition-fast);
}

.quick-btn:hover {
  border-color: var(--color-primary-light);
  background: var(--color-primary-bg);
  transform: translateY(-1px);
  box-shadow: var(--shadow-sm);
}

.quick-icon {
  font-size: 18px;
}

.message-row {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
  padding: 0 4px;
  animation: slideIn 0.3s ease-out;
}

@keyframes slideIn {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}

.message-row.user {
  justify-content: flex-end;
}

.msg-avatar {
  width: 32px;
  height: 32px;
  border-radius: var(--radius-full);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-size: 13px;
  font-weight: 700;
}

.msg-avatar.assistant {
  background: var(--color-primary-bg);
  color: var(--color-primary);
}

.msg-avatar.user {
  background: var(--color-primary);
  color: white;
}

.msg-content {
  max-width: 72%;
  min-width: 0;
}

.msg-bubble {
  padding: 12px 16px;
  border-radius: var(--radius-lg);
  font-size: 14px;
  line-height: 1.65;
  word-wrap: break-word;
}

.msg-bubble.user {
  background: var(--color-primary);
  color: white;
  border-bottom-right-radius: var(--radius-sm);
}

.msg-bubble.assistant {
  background: var(--color-bg-elevated);
  color: var(--color-text);
  border: 1px solid var(--color-border-light);
  border-bottom-left-radius: var(--radius-sm);
}

.msg-bubble.streaming {
  border-color: var(--color-primary-light);
}

.msg-text :deep(code) {
  font-family: var(--font-mono);
  font-size: 13px;
  background: rgba(0,0,0,0.06);
  padding: 2px 6px;
  border-radius: 4px;
}

.msg-bubble.user .msg-text :deep(code) {
  background: rgba(255,255,255,0.15);
}

.msg-text :deep(pre) {
  margin: 8px 0;
  padding: 12px;
  background: #1e293b;
  color: #e2e8f0;
  border-radius: var(--radius-md);
  overflow-x: auto;
}

.msg-text :deep(pre code) {
  background: none;
  padding: 0;
  color: inherit;
}

.streaming-cursor {
  display: inline-flex;
  gap: 3px;
  margin-left: 4px;
  vertical-align: middle;
}

.streaming-cursor span {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--color-primary);
  animation: pulse-dot 1.4s infinite ease-in-out;
}

.streaming-cursor span:nth-child(2) {
  animation-delay: 0.2s;
}

.streaming-cursor span:nth-child(3) {
  animation-delay: 0.4s;
}

.msg-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
  padding: 0 4px;
}

.meta-agent {
  font-size: 11px;
  font-weight: 600;
  color: var(--color-primary);
  background: var(--color-primary-bg);
  padding: 2px 8px;
  border-radius: var(--radius-full);
}

.meta-mode {
  font-size: 11px;
  color: var(--color-text-tertiary);
  background: var(--color-bg);
  padding: 2px 8px;
  border-radius: var(--radius-full);
}

.msg-feedback {
  margin-left: auto;
  display: flex;
  gap: 2px;
  opacity: 0;
  transition: opacity var(--transition-fast);
}

.message-row:hover .msg-feedback {
  opacity: 1;
}

.feedback-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-sm);
  color: var(--color-text-tertiary);
  transition: all var(--transition-fast);
}

.feedback-btn:hover {
  background: var(--color-bg);
  color: var(--color-text-secondary);
}

.feedback-btn.active {
  color: var(--color-primary);
  background: var(--color-primary-bg);
}

.chat-input-area {
  padding: 16px 0 4px;
}

.input-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.input-wrapper {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  background: var(--color-bg-elevated);
  border: 1.5px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 8px 12px;
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
}

.input-wrapper:focus-within {
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px var(--color-primary-bg);
}

.chat-textarea {
  flex: 1;
  border: none;
  outline: none;
  resize: none;
  font-size: 14px;
  line-height: 1.5;
  color: var(--color-text);
  background: transparent;
  max-height: 160px;
  padding: 4px 0;
}

.chat-textarea::placeholder {
  color: var(--color-text-tertiary);
}

.btn-send {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: var(--radius-md);
  background: var(--color-primary);
  color: white;
  transition: all var(--transition-fast);
  flex-shrink: 0;
}

.btn-send:hover:not(:disabled) {
  background: var(--color-primary-dark);
  transform: scale(1.05);
}

.btn-send:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.input-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 4px 0;
}

.session-info {
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--color-text-tertiary);
}

@media (max-width: 768px) {
  .history-panel {
    display: none;
  }

  .chat-main {
    padding: 0;
  }

  .quick-actions {
    grid-template-columns: 1fr;
  }

  .msg-content {
    max-width: 85%;
  }
}
</style>

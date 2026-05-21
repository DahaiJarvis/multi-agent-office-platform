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
        <!-- 断线重连提示 -->
        <div v-if="sseDisconnected" class="reconnect-banner">
          <span>连接已断开</span>
          <button class="reconnect-btn" @click="reconnectSSE">重新连接</button>
        </div>

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

        <!-- 任务进度面板 - 跟在消息后面 -->
        <div v-if="chatStore.executionId && chatStore.taskSteps.length > 0" class="message-row assistant">
          <div class="msg-avatar assistant">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
              <path d="M8 1a7 7 0 100 14A7 7 0 008 1zM5.5 7a1 1 0 110-2 1 1 0 010 2zm5 0a1 1 0 110-2 1 1 0 010 2zm-5.13 2.84a.5.5 0 01.76-.66A3.5 3.5 0 008 10.5a3.5 3.5 0 002.87-1.32.5.5 0 01.76.66A4.5 4.5 0 018 11.5a4.5 4.5 0 01-3.63-1.66z" />
            </svg>
          </div>
          <div class="msg-content">
            <div class="msg-bubble assistant task-progress-panel">
              <div class="task-progress-header">
                <span class="task-progress-title">任务执行进度</span>
                <span class="task-progress-summary">{{ completedStepCount }}/{{ chatStore.totalSteps || chatStore.taskSteps.length }} 已完成</span>
                <span class="task-status-badge" :class="chatStore.taskStatus">
                  {{ taskStatusLabel }}
                </span>
              </div>
              <div class="task-steps-list">
                <div
                  v-for="(step, idx) in chatStore.taskSteps"
                  :key="idx"
                  class="task-step-item"
                  :class="getStepDisplayClass(step)"
                >
                  <span class="step-status-tag" :class="getStepDisplayClass(step)">
                    {{ getStepStatusTag(step) }}
                  </span>
                  <span class="step-name-text">{{ step.step_name }}</span>
                  <span v-if="step.agent_name && step.step_type === 'agent_call'" class="step-agent-tag">{{ step.agent_name }}</span>
                  <div v-if="step.error" class="step-error-inline">{{ step.error }}</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 人工确认 - 内联显示在对话流中 -->
        <div v-if="chatStore.waitingConfirm && chatStore.confirmInfo" class="message-row assistant">
          <div class="msg-avatar assistant">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
              <path d="M8 1a7 7 0 100 14A7 7 0 008 1zM5.5 7a1 1 0 110-2 1 1 0 010 2zm5 0a1 1 0 110-2 1 1 0 010 2zm-5.13 2.84a.5.5 0 01.76-.66A3.5 3.5 0 008 10.5a3.5 3.5 0 002.87-1.32.5.5 0 01.76.66A4.5 4.5 0 018 11.5a4.5 4.5 0 01-3.63-1.66z" />
            </svg>
          </div>
          <div class="msg-content">
            <div class="msg-bubble assistant confirm-inline-card">
              <div class="confirm-inline-header">
                <span class="confirm-type-badge" :class="chatStore.confirmInfo.confirmType">
                  {{ confirmTypeLabel }}
                </span>
                <span class="confirm-inline-title">需要人工确认</span>
              </div>
              <div class="confirm-inline-body">
                <p class="confirm-reason">{{ chatStore.confirmInfo.confirmReason }}</p>
                <div class="confirm-options">
                  <button
                    v-for="opt in (chatStore.confirmInfo.options.length > 0 ? chatStore.confirmInfo.options : defaultConfirmOptions)"
                    :key="opt.value"
                    class="confirm-option-btn"
                    :class="opt.value"
                    @click="handleConfirm(opt.value)"
                    :disabled="confirmLoading"
                  >
                    <span class="option-label">{{ opt.label }}</span>
                    <span v-if="opt.description" class="option-desc">{{ opt.description }}</span>
                  </button>
                </div>
              </div>
              <div v-if="confirmLoading" class="confirm-loading">处理中...</div>
            </div>
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
import { ref, nextTick, onMounted, onUnmounted, watch, computed } from 'vue'
import { ElMessage } from 'element-plus'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { useAuthStore } from '../../stores/auth'
import { useChatStore, type Message } from '../../stores/chat'
import { agentApi, type ConfirmOption, type TaskStepStatus } from '../../api/agent'
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
const confirmLoading = ref(false)
const sseDisconnected = ref(false)
let sseEventSource: EventSource | null = null
let sseReconnectTimer: ReturnType<typeof setTimeout> | null = null

const defaultConfirmOptions: ConfirmOption[] = [
  { label: '继续执行', value: 'continue', description: '跳过当前步骤，继续后续流程' },
  { label: '重试', value: 'retry', description: '重新执行当前步骤' },
  { label: '跳过', value: 'skip', description: '标记为跳过，继续执行' },
  { label: '取消任务', value: 'cancel', description: '终止整个任务执行' },
]

const taskStatusLabel = computed(() => {
  const statusMap: Record<string, string> = {
    running: '执行中',
    completed: '已完成',
    paused: '已暂停',
    interrupted: '已中断',
    failed: '执行失败',
    cancelled: '已取消',
  }
  return statusMap[chatStore.taskStatus] || chatStore.taskStatus
})

const completedStepCount = computed(() => {
  return chatStore.taskSteps.filter(
    (s) => s.status === 'completed' || s.status === 'skipped',
  ).length
})

function getStepDisplayClass(step: TaskStepStatus): string {
  if (step.status === 'completed') return 'completed'
  if (step.status === 'skipped') return 'skipped'
  if (step.status === 'running') return 'running'
  if (step.status === 'waiting_confirm') return 'waiting_confirm'
  if (step.status === 'failed') return 'failed'
  if (step.status === 'degraded') return 'completed'
  return 'pending'
}

function getStepStatusTag(step: TaskStepStatus): string {
  if (step.status === 'completed') return '已完成'
  if (step.status === 'skipped') return '已跳过'
  if (step.status === 'running') return '进行中'
  if (step.status === 'waiting_confirm') return '待确认'
  if (step.status === 'failed') return '已失败'
  if (step.status === 'degraded') return '已降级'
  return '待执行'
}

const confirmTypeLabel = computed(() => {
  if (!chatStore.confirmInfo) return ''
  const typeMap: Record<string, string> = {
    sensitive_action: '敏感操作确认',
    degradation_decision: '降级决策',
    partial_failure: '部分失败处理',
  }
  return typeMap[chatStore.confirmInfo.confirmType] || '人工确认'
})

const quickActions = [
  { icon: '📋', text: '帮我查看今天的待办事项' },
  { icon: '📊', text: '查询本月销售数据' },
  { icon: '📅', text: '安排明天下午的会议' },
  { icon: '📧', text: '帮我起草一封项目周报邮件' },
]

marked.setOptions({
  breaks: true,
  gfm: true,
})

function renderMarkdown(text: string): string {
  if (!text) return ''
  const html = marked.parse(text) as string
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS: [
      'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
      'p', 'br', 'hr',
      'strong', 'em', 'del', 'ins',
      'ul', 'ol', 'li',
      'blockquote', 'pre', 'code',
      'a', 'img',
      'table', 'thead', 'tbody', 'tr', 'th', 'td',
      'span', 'div',
    ],
    ALLOWED_ATTR: ['href', 'target', 'rel', 'class', 'id', 'alt', 'src', 'title'],
  })
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
// 确认卡片和任务进度面板出现时也自动滚动到底部
watch(() => chatStore.waitingConfirm, scrollToBottom)
watch(() => chatStore.taskSteps.length, scrollToBottom)

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
  let execId = ''
  console.log("session_id", chatStore)
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
        } catch {
          console.warn('意图数据解析失败', data.data)
        }
      } else if (data.event === 'execution_id') {
        execId = data.data
        chatStore.setExecutionId(execId)
        chatStore.setTaskStatus('running')
        subscribeTaskEvents(execId)
      } else if (data.event === 'chunk') {
        chatStore.appendToStreamingMessage(streamingId, data.data)
        scrollToBottom()
      } else if (data.event === 'tool_call') {
        // 工具调用事件：展示Agent正在调用工具
        try {
          const toolData = typeof data.data === 'string' ? JSON.parse(data.data) : data.data
          if (toolData.tools && toolData.tools.length > 0) {
            const toolNames = toolData.tools.join(', ')
            chatStore.appendToStreamingMessage(streamingId, `\n> 正在调用工具: ${toolNames}...\n`)
            scrollToBottom()
          }
        } catch { /* ignore */ }
      } else if (data.event === 'tool_result') {
        // 工具结果事件：展示工具执行结果摘要
        try {
          const resultData = typeof data.data === 'string' ? JSON.parse(data.data) : data.data
          if (resultData.is_error) {
            chatStore.appendToStreamingMessage(streamingId, `\n> 工具执行失败\n`)
          }
        } catch { /* ignore */ }
      } else if (data.event === 'handoff') {
        // Agent切换事件
        try {
          const handoffData = typeof data.data === 'string' ? JSON.parse(data.data) : data.data
          if (handoffData.to_agent) {
            agentName = handoffData.to_agent
            chatStore.appendToStreamingMessage(streamingId, `\n> 切换到 ${handoffData.to_agent}\n`)
            scrollToBottom()
          }
        } catch { /* ignore */ }
      } else if (data.event === 'bus_event') {
        // 事件总线事件（审批、降级等）
        try {
          const busData = typeof data.data === 'string' ? JSON.parse(data.data) : data.data
          if (busData.event_type === 'approval_pending' || busData.event_type === 'human_confirm_required') {
            // 人工确认事件已通过SSE任务事件流处理
          }
        } catch { /* ignore */ }
      } else if (data.event === 'step_start') {
        // 步骤开始：在消息中展示进度
        try {
          const stepData = typeof data.data === 'string' ? JSON.parse(data.data) : data.data
          if (stepData.step_name) {
            const agentInfo = stepData.agent_name ? ` (${stepData.agent_name})` : ''
            chatStore.appendToStreamingMessage(streamingId, `\n> **步骤 ${stepData.step_index}/${stepData.total_steps}**: ${stepData.step_name}${agentInfo} - 执行中...\n`)
            scrollToBottom()
          }
        } catch { /* ignore */ }
      } else if (data.event === 'step_done') {
        // 步骤完成：更新进度状态，展示步骤结果
        try {
          const stepData = typeof data.data === 'string' ? JSON.parse(data.data) : data.data
          if (stepData.step_name) {
            const statusIcon = stepData.status === 'completed' ? '[完成]' : stepData.status === 'failed' ? '[失败]' : stepData.status === 'waiting_confirm' ? '[待确认]' : '[完成]'
            let stepMsg = `\n> **步骤 ${stepData.step_index}/${stepData.total_steps}**: ${stepData.step_name} ${statusIcon}`
            // 展示步骤的输出结果
            if (stepData.message) {
              stepMsg += `\n> ${stepData.message.substring(0, 200)}${stepData.message.length > 200 ? '...' : ''}`
            }
            if (stepData.error) {
              stepMsg += `\n> [错误] ${stepData.error}`
            }
            chatStore.appendToStreamingMessage(streamingId, stepMsg + '\n')
            scrollToBottom()
            // 刷新任务步骤列表
            refreshTaskStatus()
          }
        } catch { /* ignore */ }
      } else if (data.event === 'status' && data.data === 'completed') {
        chatStore.finalizeStreamingMessage(streamingId, { agentName, intent, mode, executionId: execId })
        chatStore.setTaskStatus('completed')
      } else if (data.event === 'error') {
        const errMsg = typeof data.data === 'string' ? data.data : (data.data?.message || data.message || '服务异常')
        chatStore.appendToStreamingMessage(streamingId, `\n\n[错误] ${errMsg}`)
        chatStore.finalizeStreamingMessage(streamingId)
        chatStore.isStreaming = false
      }
    },
    (error) => {
      const errMsg = typeof error === 'string'
        ? error
        : error?.data || error?.message || '请求失败'
      chatStore.appendToStreamingMessage(streamingId, `\n\n[错误] ${errMsg}`)
      chatStore.finalizeStreamingMessage(streamingId)
      chatStore.isStreaming = false
    },
    () => {
      // 如果消息内容为空，显示默认提示
      const streamMsg = chatStore.messages.find((m) => m.id === streamingId)
      if (streamMsg && !streamMsg.content.trim()) {
        chatStore.appendToStreamingMessage(streamingId, '任务已执行完成，请查看上方任务进度面板了解详细结果。')
      }
      chatStore.finalizeStreamingMessage(streamingId, { agentName, intent, mode, executionId: execId })
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
  } catch {
    ElMessage.error('反馈提交失败')
  }
}

function startNewChat() {
  closeSSE()
  chatStore.clearChat()
  inputRef.value?.focus()
}

async function refreshSessionList() {
  try {
    const data = await sessionApi.listUserSessions(authStore.userId, 50)
    sessionList.value = data.sessions || data || []
  } catch {
    sessionList.value = []
  }
}

async function restoreSessionAndTask(sessionId: string) {
  chatStore.setSessionId(sessionId)
  const data = await sessionApi.getHistory(sessionId)
  const historyMessages = data.messages || []
  chatStore.loadHistory(historyMessages)
  try {
    const taskStatus = await agentApi.getTaskStatusBySession(sessionId)
    if (taskStatus && taskStatus.execution_id) {
      chatStore.setExecutionId(taskStatus.execution_id)
      chatStore.updateTaskSteps(taskStatus.steps || [], taskStatus.total_steps)
      chatStore.setTaskStatus(taskStatus.status || '')
      if (taskStatus.status === 'paused') {
        const confirmStep = (taskStatus.steps || []).find(
          (s: any) => s.status === 'waiting_confirm' && s.confirm_id,
        )
        if (confirmStep && confirmStep.confirm_id) {
          chatStore.setWaitingConfirm(true)
          chatStore.confirmInfo = {
            confirmId: confirmStep.confirm_id,
            confirmType: confirmStep.confirm_type || 'sensitive_action',
            confirmReason: confirmStep.confirm_reason || '',
            options: confirmStep.options || [],
            stepIndex: confirmStep.step_index,
          }
        }
      }
      if (taskStatus.status === 'running') {
        subscribeTaskEvents(taskStatus.execution_id)
      }
    }
  } catch {
    // 任务状态查询失败不影响消息恢复
  }
  scrollToBottom()
}

async function loadSession(sessionId: string) {
  if (chatStore.isStreaming) return
  if (chatStore.sessionId === sessionId) return

  try {
    chatStore.clearChat()
    await restoreSessionAndTask(sessionId)
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
  if (session.title) {
    return session.title
  }
  if (session.first_message) {
    const text = session.first_message.trim()
    return text.length > 20 ? text.slice(0, 20) + '...' : text
  }
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
    const parsedContent = res?.results?.map((r: ParseResultItem) => r.content || r.text || '').join('\n\n')
    if (parsedContent) {
      inputText.value = inputText.value
        ? `${inputText.value}\n\n[文件内容]\n${parsedContent}`
        : `[文件内容]\n${parsedContent}`
    }
  } catch (err) {
    ElMessage.error('文件解析失败')
  }
}

function subscribeTaskEvents(execId: string) {
  closeSSE()

  const es = agentApi.subscribeTaskEvents(execId)
  if (!es) return
  sseEventSource = es
  sseDisconnected.value = false

  // 已处理过的确认ID，防止SSE事件重复触发弹窗
  // 使用全局 resolvedConfirmIds 集合（跨订阅共享）

  es.addEventListener('human_confirm_required', (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data)
      const payload = data.data || data
      if (payload.confirm_id) {
        // 如果该确认ID已被处理过，则忽略（避免重复弹窗）
        if (chatStore.resolvedConfirmIds.has(payload.confirm_id)) return
        chatStore.setWaitingConfirm(true)
        chatStore.confirmInfo = {
          confirmId: payload.confirm_id,
          confirmType: payload.confirm_type || 'sensitive_action',
          confirmReason: payload.confirm_reason || '',
          options: payload.options || [],
          stepIndex: payload.step_index || 0,
        }
      }
    } catch { /* ignore */ }
  })

  es.addEventListener('step_completed', (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data)
      refreshTaskStatus()
    } catch { /* ignore */ }
  })

  es.addEventListener('step_failed', (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data)
      refreshTaskStatus()
    } catch { /* ignore */ }
  })

  es.addEventListener('task_completed', (e: MessageEvent) => {
    chatStore.setTaskStatus('completed')
    chatStore.setWaitingConfirm(false)
    closeSSE()
  })

  es.addEventListener('task_paused', (e: MessageEvent) => {
    chatStore.setTaskStatus('paused')
    refreshTaskStatus()
  })

  es.addEventListener('task_resumed', (_e: MessageEvent) => {
    chatStore.setTaskStatus('running')
    refreshTaskStatus()
  })

  es.addEventListener('task_step_start', (_e: MessageEvent) => {
    refreshTaskStatus()
  })

  es.addEventListener('task_step_complete', (_e: MessageEvent) => {
    refreshTaskStatus()
  })

  es.addEventListener('task_interrupted', (e: MessageEvent) => {
    chatStore.setTaskStatus('interrupted')
    sseDisconnected.value = true
  })

  es.onerror = () => {
    sseDisconnected.value = true
    scheduleReconnect()
  }
}

function closeSSE() {
  if (sseEventSource) {
    sseEventSource.close()
    sseEventSource = null
  }
  if (sseReconnectTimer) {
    clearTimeout(sseReconnectTimer)
    sseReconnectTimer = null
  }
}

function scheduleReconnect() {
  if (sseReconnectTimer) return
  sseReconnectTimer = setTimeout(() => {
    sseReconnectTimer = null
    if (chatStore.executionId) {
      subscribeTaskEvents(chatStore.executionId)
    }
  }, 5000)
}

function reconnectSSE() {
  sseDisconnected.value = false
  if (chatStore.executionId) {
    subscribeTaskEvents(chatStore.executionId)
  }
}

async function refreshTaskStatus() {
  if (!chatStore.executionId) return
  try {
    const status = await agentApi.getTaskStatus(chatStore.executionId)
    if (status) {
      chatStore.updateTaskSteps(status.steps || [], status.total_steps)
      chatStore.setTaskStatus(status.status || '')
    }
  } catch { /* ignore */ }
}

async function handleConfirm(decision: string) {
  if (!chatStore.confirmInfo) return
  confirmLoading.value = true

  // 记录已确认的 confirm_id，防止 SSE 事件重复触发弹窗
  const confirmedId = chatStore.confirmInfo.confirmId

  try {
    await agentApi.confirmTask(
      chatStore.confirmInfo.confirmId,
      decision,
      '',
      authStore.userId,
      decision === 'retry' ? chatStore.confirmInfo.options?.[0]?.value : undefined,
      chatStore.executionId,
      chatStore.confirmInfo.stepIndex,
    )
    // 标记此 confirm_id 已处理，后续 SSE 事件和 updateTaskSteps 不会重复弹窗
    chatStore.resolvedConfirmIds.add(confirmedId)
    // 先关闭确认弹窗
    chatStore.setWaitingConfirm(false)
    ElMessage.success('确认已提交')

    // 确认后立即订阅 SSE，后端已改为后台异步执行，事件会通过 SSE 推送
    if (chatStore.executionId) {
      subscribeTaskEvents(chatStore.executionId)
    }

    // 启动轮询作为 SSE 的补充，确保任务进度更新
    let pollCount = 0
    const maxPolls = 30
    const pollInterval = setInterval(async () => {
      pollCount++
      if (pollCount > maxPolls || chatStore.taskStatus === 'completed' || chatStore.taskStatus === 'cancelled') {
        clearInterval(pollInterval)
        return
      }
      await refreshTaskStatus()
    }, 2000)
  } catch {
    ElMessage.error('确认提交失败')
  } finally {
    confirmLoading.value = false
  }
}

onMounted(async () => {
  inputRef.value?.focus()
  refreshSessionList()

  // 恢复任务状态
  const { recovered, sessionId: recoveredSessionId } = await chatStore.recoverTaskStatus()
  if (recovered && chatStore.executionId) {
    // 恢复会话消息
    if (recoveredSessionId) {
      try {
        const data = await sessionApi.getHistory(recoveredSessionId)
        const historyMessages = data.messages || []
        chatStore.loadHistory(historyMessages)
        scrollToBottom()
      } catch {
        // 消息加载失败不影响任务状态恢复
      }
    }
    // 仅运行中的任务才订阅SSE，已完成/已暂停/已中断的不需要
    if (chatStore.taskStatus === 'running') {
      subscribeTaskEvents(chatStore.executionId)
    }
    // 刷新侧边栏以高亮当前会话
    refreshSessionList()
  } else {
    // sessionStorage 中无 execution_id，尝试从最近会话恢复
    // 场景：用户关闭浏览器后重新打开，sessionStorage 已丢失
    try {
      const savedSessionId = sessionStorage.getItem('current_session_id') || localStorage.getItem('current_session_id')
      if (savedSessionId) {
        await restoreSessionAndTask(savedSessionId)
      } else {
        // 尝试从最近会话列表恢复最近的会话
        const data = await sessionApi.listUserSessions(authStore.userId, 1)
        const recentSessions = data.sessions || data || []
        if (recentSessions.length > 0 && recentSessions[0].session_id) {
          await restoreSessionAndTask(recentSessions[0].session_id)
        }
      }
    } catch {
      // 恢复失败不影响正常使用
    }
  }
})

onUnmounted(() => {
  closeSSE()
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

/* 任务进度面板 - 内联在消息气泡中 */
.task-progress-panel {
  border: 1px solid var(--color-border-light);
  background: var(--color-bg-elevated) !important;
}

.task-progress-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.task-progress-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--color-text);
}

.task-progress-summary {
  font-size: 12px;
  color: var(--color-text-secondary);
  font-weight: 500;
}

.task-status-badge {
  font-size: 12px;
  padding: 2px 10px;
  border-radius: var(--radius-full);
  font-weight: 500;
  margin-left: auto;
}

.task-status-badge.running {
  background: #e6f7ff;
  color: #1890ff;
}

.task-status-badge.completed {
  background: #f6ffed;
  color: #52c41a;
}

.task-status-badge.paused {
  background: #fff7e6;
  color: #fa8c16;
}

.task-status-badge.interrupted {
  background: #fff1f0;
  color: #f5222d;
}

.task-status-badge.failed {
  background: #fff1f0;
  color: #f5222d;
}

.task-status-badge.cancelled {
  background: #f5f5f5;
  color: #999;
}

.task-steps-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.task-step-item {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 6px 10px;
  border-radius: var(--radius-md);
  background: var(--color-bg);
  transition: background var(--transition-fast);
  flex-wrap: wrap;
}

.task-step-item.running {
  background: #e6f7ff;
}

.task-step-item.failed {
  background: #fff1f0;
}

.task-step-item.waiting_confirm {
  background: #fff7e6;
}

.step-status-tag {
  font-size: 11px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 3px;
  white-space: nowrap;
  flex-shrink: 0;
}

.step-status-tag.completed {
  background: #f0f9eb;
  color: #52c41a;
}

.step-status-tag.skipped {
  background: #f5f5f5;
  color: #999;
}

.step-status-tag.running {
  background: #e6f7ff;
  color: #1890ff;
}

.step-status-tag.waiting_confirm {
  background: #fff7e6;
  color: #fa8c16;
}

.step-status-tag.failed {
  background: #fff1f0;
  color: #f5222d;
}

.step-status-tag.pending {
  background: #f5f5f5;
  color: #bbb;
}

.step-name-text {
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text);
}

.task-step-item.pending .step-name-text {
  color: var(--color-text-tertiary);
}

.step-agent-tag {
  font-size: 11px;
  color: var(--color-text-tertiary);
  background: var(--color-bg);
  padding: 1px 6px;
  border-radius: 3px;
  border: 1px solid var(--color-border-light);
}

.step-error-inline {
  font-size: 12px;
  color: #f5222d;
  width: 100%;
  margin-top: 2px;
  padding-left: 0;
}

/* 人工确认 - 内联卡片 */
.confirm-inline-card {
  border: 1px solid #ffd591;
  background: #fffbe6 !important;
}

.confirm-inline-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.confirm-inline-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--color-text);
}

.confirm-type-badge {
  font-size: 12px;
  padding: 2px 10px;
  border-radius: var(--radius-full);
  font-weight: 500;
}

.confirm-type-badge.sensitive_action {
  background: #fff1f0;
  color: #f5222d;
}

.confirm-type-badge.degradation_decision {
  background: #fff7e6;
  color: #fa8c16;
}

.confirm-type-badge.partial_failure {
  background: #e6f7ff;
  color: #1890ff;
}

.confirm-inline-body {
  margin-bottom: 8px;
}

.confirm-reason {
  font-size: 14px;
  color: var(--color-text-secondary);
  line-height: 1.6;
  margin: 0 0 16px;
}

.confirm-options {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.confirm-option-btn {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  padding: 8px 14px;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  background: white;
  cursor: pointer;
  transition: all var(--transition-fast);
  text-align: left;
  flex: 0 0 auto;
}

.confirm-option-btn:hover:not(:disabled) {
  border-color: var(--color-primary);
  background: var(--color-primary-bg);
}

.confirm-option-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.confirm-option-btn.cancel {
  border-color: #ffccc7;
}

.confirm-option-btn.cancel:hover:not(:disabled) {
  background: #fff1f0;
  border-color: #f5222d;
}

.option-label {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text);
}

.option-desc {
  font-size: 12px;
  color: var(--color-text-tertiary);
  margin-top: 2px;
}

.confirm-loading {
  text-align: center;
  font-size: 13px;
  color: var(--color-text-secondary);
  padding: 8px 0;
}

/* 断线重连提示 */
.reconnect-banner {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 10px 16px;
  background: #fff7e6;
  border: 1px solid #ffe58f;
  border-radius: var(--radius-md);
  margin-bottom: 16px;
  font-size: 13px;
  color: #ad6800;
}

.reconnect-btn {
  padding: 4px 12px;
  border-radius: var(--radius-sm);
  background: #fa8c16;
  color: white;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: background var(--transition-fast);
}

.reconnect-btn:hover {
  background: #d46b08;
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

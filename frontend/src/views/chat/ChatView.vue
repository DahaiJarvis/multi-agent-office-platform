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
          <!-- 用户消息：右侧气泡 -->
          <div v-if="msg.role === 'user'" class="msg-content user-content">
            <div class="msg-bubble user">
              <div class="msg-text" v-html="renderMarkdown(msg.content)" />
            </div>
          </div>

          <!-- AI 消息：左侧 Trae 风格流式展示 -->
          <div v-if="msg.role === 'assistant'" class="msg-content assistant-content">
            <!-- AI 文字回复区域 -->
            <div v-if="msg.content.trim()" class="assistant-reply">
              <div class="msg-text" v-html="renderMarkdown(msg.content)" />
              <div v-if="msg.streaming" class="streaming-cursor">
                <span /><span /><span />
              </div>
            </div>

            <!-- 穿插时间线：任务看板 + 文字交替显示 -->
            <template v-if="isLastAssistantMsg(msg.id) && interleavedTimeline.length > 0">
              <template v-for="(item, tIdx) in interleavedTimeline" :key="tIdx">
                <!-- 任务看板快照 -->
                <div v-if="item.type === 'board'" class="task-board-card">
                  <div class="task-board-header">
                    <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" class="board-icon">
                      <path d="M1 2.5A1.5 1.5 0 012.5 1h3A1.5 1.5 0 017 2.5v3A1.5 1.5 0 015.5 7h-3A1.5 1.5 0 011 5.5v-3zm2 0v3h3v-3h-3zm8-1.5A1.5 1.5 0 009.5 2.5v3A1.5 1.5 0 0011 7h3a1.5 1.5 0 001.5-1.5v-3A1.5 1.5 0 0014 1h-3zm0 1.5h3v3h-3v-3zM1 10.5A1.5 1.5 0 012.5 9h3A1.5 1.5 0 017 10.5v3A1.5 1.5 0 015.5 15h-3A1.5 1.5 0 011 13.5v-3zm2 0v3h3v-3h-3z" />
                    </svg>
                    <span class="board-title">任务看板</span>
                    <span class="board-progress">{{ item.completedCount }}/{{ item.totalCount }} 已完成</span>
                    <span class="task-status-badge" :class="item.taskStatus || 'running'">
                      {{ getBoardStatusLabel(item.taskStatus) }}
                    </span>
                  </div>
                  <div class="board-steps">
                    <div
                      v-for="step in item.steps"
                      :key="step.step_index"
                      class="board-step"
                      :class="step.status"
                    >
                      <div class="board-step-indicator">
                        <svg v-if="step.status === 'completed'" width="12" height="12" viewBox="0 0 16 16" fill="currentColor" class="step-icon-done">
                          <path d="M13.78 4.22a.75.75 0 010 1.06l-7.25 7.25a.75.75 0 01-1.06 0L2.22 9.28a.75.75 0 011.06-1.06L6 10.94l6.72-6.72a.75.75 0 011.06 0z" />
                        </svg>
                        <svg v-else-if="step.status === 'failed'" width="12" height="12" viewBox="0 0 16 16" fill="currentColor" class="step-icon-fail">
                          <path d="M3.72 3.72a.75.75 0 011.06 0L8 6.94l3.22-3.22a.75.75 0 111.06 1.06L9.06 8l3.22 3.22a.75.75 0 11-1.06 1.06L8 9.06l-3.22 3.22a.75.75 0 01-1.06-1.06L6.94 8 3.72 4.78a.75.75 0 010-1.06z" />
                        </svg>
                        <span v-else-if="step.status === 'running'" class="step-spinner"></span>
                        <span v-else class="step-dot"></span>
                      </div>
                      <span class="board-step-name">{{ step.step_name || ('步骤 ' + step.step_index) }}</span>
                      <span v-if="step.agent_name" class="board-step-agent">{{ step.agent_name }}</span>
                    </div>
                  </div>
                </div>

                <!-- 文字内容：思考/工具调用等 -->
                <div v-else-if="item.type === 'text'" class="timeline-text-item" :class="'text-type-' + item.activity.type">
                  <div v-if="item.activity.type === 'thought'" class="thought-inline">
                    <svg class="thought-icon" width="12" height="12" viewBox="0 0 16 16" fill="currentColor">
                      <path d="M8 1.5a6.5 6.5 0 100 13 6.5 6.5 0 000-13zM0 8a8 8 0 1116 0A8 8 0 010 8zm6.5-.5A1.5 1.5 0 118 6a1.5 1.5 0 01-1.5 1.5zm3.5 0A1.5 1.5 0 1111.5 6 1.5 1.5 0 0110 7.5zM5.17 9.84a.5.5 0 01.73-.68A3.5 3.5 0 008 10.5a3.5 3.5 0 002.1-.34.5.5 0 11.4.92A4.5 4.5 0 018 11.5a4.5 4.5 0 01-2.83-1.66z" />
                    </svg>
                    <span v-if="item.activity.agentName" class="text-agent">{{ item.activity.agentName }}</span>
                    <span v-if="item.activity.reasoningType" class="reasoning-type-badge" :class="'reasoning-' + item.activity.reasoningType">{{ getReasoningTypeLabel(item.activity.reasoningType) }}</span>
                    <span class="thought-content">{{ item.activity.content }}</span>
                    <button v-if="item.activity.reasoningChain" class="reasoning-toggle-btn" @click="toggleReasoning(item.id)">
                      {{ expandedReasonings.has(item.id) ? '收起推理' : '查看推理' }}
                    </button>
                    <div v-if="item.activity.reasoningChain && expandedReasonings.has(item.id)" class="reasoning-detail">
                      <pre class="reasoning-pre">{{ formatReasoningChain(item.activity.reasoningChain) }}</pre>
                    </div>
                  </div>
                  <div v-else-if="item.activity.type === 'tool_call'" class="tool-inline">
                    <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" class="tool-icon">
                      <path d="M9.5 1.1l3.4 3.4L4.8 12.6 1.4 9.2 9.5 1.1zm3.9-.5l1.9 1.9a1 1 0 010 1.4l-1.1 1.1-3.4-3.4 1.1-1.1a1 1 0 011.5 0zM.4 13.9l2.5-1 1.6 1.6-1 2.5a.5.5 0 01-.8.2l-2.5-2.5a.5.5 0 01.2-.8z" />
                    </svg>
                    <span v-if="item.activity.agentName" class="text-agent">{{ item.activity.agentName }}</span>
                    <span class="tool-content">{{ item.activity.content }}</span>
                  </div>
                  <div v-else-if="item.activity.type === 'tool_result'" class="result-inline" :class="{ 'result-failed': item.activity.status === 'failed' }">
                    <svg v-if="item.activity.status === 'failed'" width="12" height="12" viewBox="0 0 16 16" fill="currentColor" class="result-icon-fail">
                      <path d="M8 15A7 7 0 118 1a7 7 0 010 14zm0 1A8 8 0 108 0a8 8 0 000 16zM3.72 3.72a.75.75 0 011.06 0L8 6.94l3.22-3.22a.75.75 0 111.06 1.06L9.06 8l3.22 3.22a.75.75 0 11-1.06 1.06L8 9.06l-3.22 3.22a.75.75 0 01-1.06-1.06L6.94 8 3.72 4.78a.75.75 0 010-1.06z" />
                    </svg>
                    <svg v-else width="12" height="12" viewBox="0 0 16 16" fill="currentColor" class="result-icon-ok">
                      <path d="M8 15A7 7 0 118 1a7 7 0 010 14zm0 1A8 8 0 108 0a8 8 0 000 16zM6.97 11.03a.75.75 0 01-1.06 0L3.72 8.84a.75.75 0 011.06-1.06l1.66 1.66 5.08-5.08a.75.75 0 111.06 1.06l-5.61 5.61z" />
                    </svg>
                    <span class="result-content">{{ item.activity.content }}</span>
                  </div>
                  <div v-else-if="item.activity.type === 'handoff'" class="handoff-inline">
                    <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" class="handoff-icon">
                      <path d="M8 0a8 8 0 100 16A8 8 0 008 0zM4.5 7.5a.75.75 0 000 1.5h5.19l-1.72 1.72a.75.75 0 101.06 1.06l3-3a.75.75 0 000-1.06l-3-3a.75.75 0 00-1.06 1.06L9.69 7.5H4.5z" />
                    </svg>
                    <span class="handoff-content">{{ item.activity.content }}</span>
                  </div>
                  <div v-else class="generic-inline">
                    <span v-if="item.activity.agentName" class="text-agent">{{ item.activity.agentName }}</span>
                    <span class="generic-content">{{ item.activity.content }}</span>
                    <button v-if="item.activity.reasoningChain" class="reasoning-toggle-btn" @click="toggleReasoning(item.id)">
                      {{ expandedReasonings.has(item.id) ? '收起推理' : '查看推理' }}
                    </button>
                  </div>
                  <div v-if="item.activity.reasoningChain && expandedReasonings.has(item.id) && item.activity.type !== 'thought'" class="reasoning-detail">
                    <pre class="reasoning-pre">{{ formatReasoningChain(item.activity.reasoningChain) }}</pre>
                  </div>
                </div>
              </template>
            </template>

            <!-- 任务最终结果 -->
            <div v-if="isLastAssistantMsg(msg.id) && taskFinalResult" class="task-result-block">
              <div class="task-result-header">
                <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
                  <path d="M8 16A8 8 0 108 0a8 8 0 000 16zm3.78-9.72a.75.75 0 00-1.06-1.06L6.75 9.19 5.28 7.72a.75.75 0 00-1.06 1.06l2 2a.75.75 0 001.06 0l4.5-4.5z" />
                </svg>
                <span class="task-result-title">执行结果</span>
                <span v-if="taskFinalResult.agentName" class="task-result-agent">{{ taskFinalResult.agentName }}</span>
              </div>
              <div class="task-result-content" v-html="renderMarkdown(taskFinalResult.content)" />
            </div>

            <!-- 消息元信息 -->
            <div v-if="!msg.streaming" class="msg-meta">
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
        </div>

        <!-- 人工确认 - 内联显示在对话流中 -->
        <div v-if="chatStore.waitingConfirm && chatStore.confirmInfo" class="message-row assistant">
          <div class="msg-content assistant-content">
            <div class="confirm-inline-card">
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
        <!-- 任务暂停操作栏 -->
        <div v-if="isTaskInterrupted" class="task-action-bar">
          <span class="task-action-hint">任务已暂停 - 可补充需求后继续，或放弃任务</span>
          <div class="task-action-buttons">
            <button class="btn-task-action btn-resume" @click="handleResumeTask">
              继续执行
            </button>
            <button class="btn-task-action btn-abandon" @click="handleAbandonTask">
              放弃任务
            </button>
          </div>
        </div>

        <div class="input-toolbar">
          <KbSelector @select="handleKbSelect" />
          <FileUploader @upload="handleFileUpload" />
        </div>
        <div class="input-wrapper">
          <textarea
            ref="inputRef"
            v-model="inputText"
            class="chat-textarea"
            :placeholder="isTaskInterrupted ? '输入补充需求（可选），按 Enter 继续...' : '输入消息，按 Enter 发送...'"
            rows="1"
            :disabled="chatStore.isStreaming && !isTaskInterrupted"
            @keydown.enter.exact.prevent="handleInputEnter"
            @input="autoResize"
          />
          <!-- 执行中：停止按钮 -->
          <button
            v-if="chatStore.isStreaming && !isTaskInterrupted"
            class="btn-stop"
            @click="handleStopTask"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
              <rect x="3" y="3" width="10" height="10" rx="1" />
            </svg>
          </button>
          <!-- 暂停状态：继续按钮 -->
          <button
            v-else-if="isTaskInterrupted"
            class="btn-resume-send"
            :disabled="false"
            @click="handleResumeTask"
          >
            继续
          </button>
          <!-- 正常状态：发送按钮 -->
          <button
            v-else
            class="btn-send"
            :disabled="!inputText.trim()"
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
import { useChatStore, type Message, type TaskActivity } from '../../stores/chat'
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
const runningStepInfo = ref<{ step_index: number; step_name: string; agent_name: string; total_steps: number } | null>(null)
const taskFinalResult = ref<{ content: string; agentName: string } | null>(null)
let sseEventSource: EventSource | null = null
let sseReconnectTimer: ReturnType<typeof setTimeout> | null = null

const defaultConfirmOptions: ConfirmOption[] = [
  { label: '继续执行', value: 'continue', description: '跳过当前步骤，继续后续流程' },
  { label: '重试', value: 'retry', description: '重新执行当前步骤' },
  { label: '跳过', value: 'skip', description: '标记为跳过，继续执行' },
  { label: '取消任务', value: 'cancel', description: '终止整个任务执行' },
]

const showTaskProgress = computed(() => {
  return chatStore.isStreaming || chatStore.taskActivities.length > 0 || (chatStore.executionId && chatStore.taskSteps.length > 0) || runningStepInfo.value !== null
})

const lastAssistantMsgId = computed(() => {
  for (let i = chatStore.messages.length - 1; i >= 0; i--) {
    if (chatStore.messages[i].role === 'assistant') {
      return chatStore.messages[i].id
    }
  }
  return ''
})

function isLastAssistantMsg(msgId: string): boolean {
  return msgId === lastAssistantMsgId.value
}

const isTaskInterrupted = computed(() => {
  return chatStore.taskStatus === 'interrupted'
})

const sortedActivities = computed(() => {
  return chatStore.taskActivities.filter(
    (a) => a.type !== 'step_start' && a.type !== 'step_done',
  )
})

const thoughtActivities = computed(() => {
  return chatStore.taskActivities.filter((a) => a.type === 'thought')
})

const nonThoughtActivities = computed(() => {
  return chatStore.taskActivities.filter(
    (a) => a.type !== 'step_start' && a.type !== 'step_done' && a.type !== 'thought',
  )
})

const thoughtExpanded = ref(true)

function toggleThinking() {
  thoughtExpanded.value = !thoughtExpanded.value
}

// 推理链折叠展开状态
const expandedReasonings = ref<Set<string>>(new Set())

function toggleReasoning(activityId: string) {
  const s = new Set(expandedReasonings.value)
  if (s.has(activityId)) {
    s.delete(activityId)
  } else {
    s.add(activityId)
  }
  expandedReasonings.value = s
}

function getReasoningTypeLabel(type: string): string {
  const labels: Record<string, string> = {
    intent: '意图分析',
    planning: '任务规划',
    review: '审核决策',
    tool_selection: '工具选择',
    execution: '执行推理',
  }
  return labels[type] || '推理'
}

function formatReasoningChain(chainJson: string): string {
  if (!chainJson) return ''
  try {
    const chain = JSON.parse(chainJson)
    if (chain.steps && Array.isArray(chain.steps)) {
      return chain.steps.map((step: any, i: number) => {
        const typeLabel = getReasoningTypeLabel(step.reasoning_type || '')
        return `[${typeLabel}] ${step.agent_name || ''}\n${step.thought_process || step.conclusion || ''}`
      }).join('\n\n')
    }
    return chainJson
  } catch {
    return chainJson
  }
}

interface BoardStepSnapshot {
  step_index: number
  step_name: string
  agent_name: string
  status: string
}

interface TimelineBoardItem {
  type: 'board'
  completedCount: number
  totalCount: number
  steps: BoardStepSnapshot[]
  taskStatus: string
}

interface TimelineTextItem {
  type: 'text'
  activity: TaskActivity
}

type TimelineItem = TimelineBoardItem | TimelineTextItem

const interleavedTimeline = computed(() => {
  const timeline: TimelineItem[] = []
  const allSteps = sortedTaskSteps.value
  const totalCount = allSteps.length || chatStore.totalSteps || 0

  if (totalCount === 0 && chatStore.taskActivities.length === 0) {
    return timeline
  }

  const completedStepIndices = new Set<number>()

  const buildBoardSnapshot = (): TimelineBoardItem => {
    const steps: BoardStepSnapshot[] = allSteps.map((s) => ({
      step_index: s.step_index,
      step_name: s.step_name,
      agent_name: s.agent_name || '',
      status: completedStepIndices.has(s.step_index) ? 'completed' : s.status,
    }))
    if (steps.length === 0 && totalCount > 0) {
      for (let i = 1; i <= totalCount; i++) {
        steps.push({
          step_index: i,
          step_name: '',
          agent_name: '',
          status: completedStepIndices.has(i) ? 'completed' : 'pending',
        })
      }
    }
    return {
      type: 'board',
      completedCount: completedStepIndices.size,
      totalCount,
      steps,
      taskStatus: chatStore.taskStatus || 'running',
    }
  }

  // 先收集文字活动，同时跟踪步骤完成状态
  for (const act of chatStore.taskActivities) {
    if (act.type === 'step_done') {
      if (act.stepIndex != null) {
        completedStepIndices.add(act.stepIndex)
      }
      // step_done 携带推理链时，在时间线中展示推理折叠
      if (act.reasoningChain) {
        timeline.push({ type: 'text', activity: act })
      }
    } else if (act.type === 'step_start') {
      // step_start 不生成看板快照，仅触发步骤状态变更
    } else if (act.type === 'thought') {
      timeline.push({ type: 'text', activity: act })
    } else if (act.type === 'tool_call' || act.type === 'tool_result') {
      timeline.push({ type: 'text', activity: act })
    } else if (act.type === 'handoff') {
      timeline.push({ type: 'text', activity: act })
    } else if (act.type === 'intent') {
      timeline.push({ type: 'text', activity: act })
    }
  }

  // 始终只保留一个最新的看板快照，放在时间线最前面
  const boardSnapshot = buildBoardSnapshot()
  if (boardSnapshot.steps.length > 0) {
    timeline.unshift(boardSnapshot)
  }

  return timeline
})

const sortedTaskSteps = computed(() => {
  const steps = [...chatStore.taskSteps]
  if (runningStepInfo.value) {
    const runningIdx = runningStepInfo.value.step_index
    const alreadyExists = steps.some((s) => s.step_index === runningIdx)
    if (!alreadyExists) {
      steps.push({
        step_index: runningIdx,
        step_name: runningStepInfo.value.step_name,
        step_type: 'agent_call',
        agent_name: runningStepInfo.value.agent_name,
        status: 'running',
        error: '',
        fallback_used: '',
      })
    }
  }
  const statusOrder: Record<string, number> = {
    completed: 0,
    skipped: 0,
    failed: 0,
    degraded: 0,
    waiting_confirm: 1,
    running: 2,
    pending: 3,
  }
  return steps.sort((a, b) => {
    const orderA = statusOrder[a.status] ?? 1
    const orderB = statusOrder[b.status] ?? 1
    if (orderA !== orderB) return orderA - orderB
    return a.step_index - b.step_index
  })
})

const activityStatusLabel = computed(() => {
  if (chatStore.taskStatus === 'completed') return '已完成'
  if (chatStore.taskStatus === 'failed') return '执行失败'
  if (chatStore.taskStatus === 'paused') return '已暂停'
  if (chatStore.taskStatus === 'interrupted') return '已中断'
  if (chatStore.isStreaming || chatStore.taskActivities.length > 0) return '执行中'
  return chatStore.taskStatus || '执行中'
})

function getBoardStatusLabel(status: string): string {
  if (status === 'completed') return '已完成'
  if (status === 'failed') return '执行失败'
  if (status === 'paused') return '已暂停'
  if (status === 'interrupted') return '已中断'
  return '执行中'
}

function getActivityIcon(act: TaskActivity): string {
  if (act.type === 'step_start') {
    if (act.status === 'completed') return '\u2705'
    if (act.status === 'failed') return '\u274C'
    if (act.status === 'waiting_confirm') return '\u23F8'
    return '\u25B6'
  }
  const iconMap: Record<string, string> = {
    intent: '\u{1F3AF}',
    thought: '\u{1F4AD}',
    tool_call: '\u{1F527}',
    tool_result: '\u{1F4CB}',
    step_done: '\u2705',
    handoff: '\u{1F504}',
    result: '\u{1F4DD}',
  }
  return iconMap[act.type] || '\u{1F4E1}'
}

function getActivityClass(act: TaskActivity): string {
  if (act.status === 'running') return 'activity-running'
  if (act.status === 'failed') return 'activity-failed'
  if (act.status === 'waiting_confirm') return 'activity-waiting'
  return 'activity-completed'
}

function isStepInterrupted(step: TaskStepStatus): boolean {
  return isTaskInterrupted.value && (step.status === 'running' || step.status === 'pending')
}

function getStepDisplayClass(step: TaskStepStatus): string {
  if (step.status === 'completed') return 'completed'
  if (step.status === 'skipped') return 'skipped'
  if (isStepInterrupted(step)) return 'interrupted'
  if (step.status === 'running') return 'running'
  if (step.status === 'waiting_confirm') return 'waiting_confirm'
  if (step.status === 'failed') return 'failed'
  if (step.status === 'degraded') return 'completed'
  return 'pending'
}

function getStepStatusTag(step: TaskStepStatus): string {
  if (step.status === 'completed') return '已完成'
  if (step.status === 'skipped') return '已跳过'
  if (isStepInterrupted(step)) return '已暂停'
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
watch(() => chatStore.taskActivities.length, scrollToBottom)
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
  taskFinalResult.value = null
  chatStore.clearTaskActivities()
  chatStore.setTaskStatus('running')
  chatStore.executionId = ''

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
        // 乐观地将新会话添加到本地列表，避免等待服务端刷新延迟
        const newSid = data.data
        const alreadyExists = sessionList.value.some((s: SessionInfo) => s.session_id === newSid)
        if (!alreadyExists) {
          sessionList.value.unshift({
            session_id: newSid,
            user_id: authStore.userId,
            channel: 'web',
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            message_count: 1,
            active_agents: [],
            first_message: text,
          })
        }
        refreshSessionList()
      } else if (data.event === 'intent') {
        try {
          const intentData = typeof data.data === 'string' ? JSON.parse(data.data) : data.data
          agentName = intentData.agent || ''
          intent = intentData.intent || ''
          mode = intentData.mode || ''
          chatStore.addTaskActivity({
            type: 'intent',
            agentName: agentName,
            content: `${intentData.intent || '分析中'} (置信度: ${(intentData.confidence * 100).toFixed(0)}%)`,
            status: 'completed',
            reasoningType: 'intent',
            reasoningChain: intentData.reasoning || '',
          })
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
            chatStore.addTaskActivity({
              type: 'tool_call',
              agentName: toolData.agent_name || agentName,
              content: `调用工具: ${toolNames}`,
              status: 'running',
            })
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
          chatStore.addTaskActivity({
            type: 'tool_result',
            agentName: resultData.agent_name || agentName,
            content: resultData.is_error ? '工具执行失败' : `${resultData.tool_name || '工具'} 执行完成`,
            status: resultData.is_error ? 'failed' : 'completed',
            detail: resultData.is_error ? undefined : (resultData.content ? resultData.content.substring(0, 150) : undefined),
          })
        } catch { /* ignore */ }
      } else if (data.event === 'handoff') {
        // Agent切换事件
        try {
          const handoffData = typeof data.data === 'string' ? JSON.parse(data.data) : data.data
          if (handoffData.to_agent) {
            agentName = handoffData.to_agent
            chatStore.appendToStreamingMessage(streamingId, `\n> 切换到 ${handoffData.to_agent}\n`)
            chatStore.addTaskActivity({
              type: 'handoff',
              agentName: handoffData.to_agent,
              content: `从 ${handoffData.from_agent || '系统'} 切换到 ${handoffData.to_agent}`,
              status: 'completed',
            })
            scrollToBottom()
          }
        } catch { /* ignore */ }
      } else if (data.event === 'thought') {
        // Agent 思考事件（含 CoT 推理信息）
        try {
          const thoughtData = typeof data.data === 'string' ? JSON.parse(data.data) : data.data
          if (thoughtData.content) {
            chatStore.addTaskActivity({
              type: 'thought',
              agentName: thoughtData.agent_name || agentName,
              content: thoughtData.content.substring(0, 200),
              status: 'completed',
              reasoningType: thoughtData.reasoning_type || 'execution',
            })
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
        try {
          const stepData = typeof data.data === 'string' ? JSON.parse(data.data) : data.data
          if (stepData.step_name) {
            const agentInfo = stepData.agent_name ? ` (${stepData.agent_name})` : ''
            chatStore.appendToStreamingMessage(streamingId, `\n> **步骤 ${stepData.step_index}/${stepData.total_steps}**: ${stepData.step_name}${agentInfo} - 执行中...\n`)
            runningStepInfo.value = {
              step_index: stepData.step_index || 0,
              step_name: stepData.step_name,
              agent_name: stepData.agent_name || '',
              total_steps: stepData.total_steps || 0,
            }
            chatStore.addTaskActivity({
              type: 'step_start',
              agentName: stepData.agent_name || agentName,
              content: stepData.step_name,
              status: 'running',
              stepIndex: stepData.step_index,
              totalSteps: stepData.total_steps,
            })
            scrollToBottom()
          }
        } catch { /* ignore */ }
      } else if (data.event === 'step_done') {
        try {
          const stepData = typeof data.data === 'string' ? JSON.parse(data.data) : data.data
          if (stepData.step_name) {
            const isWaitingConfirm = stepData.status === 'waiting_confirm'
            const statusIcon = stepData.status === 'completed' ? '[完成]' : stepData.status === 'failed' ? '[失败]' : isWaitingConfirm ? '[待确认]' : '[完成]'
            let stepMsg = `\n> **步骤 ${stepData.step_index}/${stepData.total_steps}**: ${stepData.step_name} ${statusIcon}`
            if (stepData.message) {
              stepMsg += `\n> ${stepData.message.substring(0, 200)}${stepData.message.length > 200 ? '...' : ''}`
            }
            if (stepData.error) {
              stepMsg += `\n> [错误] ${stepData.error}`
            }
            chatStore.appendToStreamingMessage(streamingId, stepMsg + '\n')
            runningStepInfo.value = null

            chatStore.addTaskActivity({
              type: 'step_done',
              agentName: stepData.agent_name || agentName,
              content: stepData.step_name,
              status: stepData.status === 'completed' ? 'completed' : stepData.status === 'failed' ? 'failed' : 'completed',
              stepIndex: stepData.step_index,
              totalSteps: stepData.total_steps,
              reasoningChain: stepData.reasoning_chain || '',
            })

            // 关键步骤（裁判裁决、汇总等）完成时，将结果存储到 taskFinalResult，在任务看板下方独立展示
            if (stepData.message && stepData.status === 'completed') {
              const isAggregateStep = stepData.step_type === 'aggregate' || stepData.step_name?.includes('裁决') || stepData.step_name?.includes('汇总') || stepData.step_name?.includes('总结')
              const isLastStep = stepData.step_index === stepData.total_steps
              if (isAggregateStep || isLastStep) {
                taskFinalResult.value = {
                  content: stepData.message,
                  agentName: stepData.agent_name || agentName,
                }
              }
            }

            if (isWaitingConfirm) {
              chatStore.setTaskStatus('paused')
            }

            scrollToBottom()
            refreshTaskStatus()
          }
        } catch { /* ignore */ }
      } else if (data.event === 'status' && data.data === 'completed') {
        if (chatStore.taskStatus !== 'paused') {
          chatStore.finalizeStreamingMessage(streamingId, { agentName, intent, mode, executionId: execId })
          chatStore.setTaskStatus('completed')
          runningStepInfo.value = null
          chatStore.addTaskActivity({
            type: 'handoff',
            agentName: agentName,
            content: '任务执行完成',
            status: 'completed',
          })
        }
      } else if (data.event === 'status' && data.data === 'paused') {
        chatStore.finalizeStreamingMessage(streamingId, { agentName, intent, mode, executionId: execId })
        chatStore.setTaskStatus('paused')
        if (execId) {
          refreshTaskStatus()
        }
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
      // 有 executionId 说明是多Agent任务，isStreaming 由 SSE 事件（task_completed/task_interrupted）控制
      if (!execId) {
        chatStore.isStreaming = false
      }
      if (chatStore.taskStatus !== 'completed' && chatStore.taskStatus !== 'failed' && chatStore.taskStatus !== 'paused' && chatStore.taskStatus !== 'interrupted') {
        if (!execId) {
          chatStore.setTaskStatus('completed')
        }
      }
      if (chatStore.executionId) {
        refreshTaskStatus()
      }
      refreshSessionList()
    },
  )
}

function handleInputEnter() {
  if (isTaskInterrupted.value) {
    handleResumeTask()
  } else {
    handleSend()
  }
}

async function handleStopTask() {
  if (!chatStore.executionId) return
  try {
    await agentApi.cancelTask(chatStore.executionId, authStore.userId, false)
    chatStore.isStreaming = false
    chatStore.setTaskStatus('interrupted')
  } catch (e: any) {
    ElMessage.error(e?.message || '暂停任务失败')
  }
}

async function handleResumeTask() {
  if (!chatStore.executionId) return
  const supplementaryMsg = inputText.value.trim()
  inputText.value = ''
  if (inputRef.value) {
    inputRef.value.style.height = 'auto'
  }

  try {
    chatStore.isStreaming = true
    chatStore.setTaskStatus('running')

    const result = await agentApi.resumeTask(
      chatStore.executionId,
      chatStore.sessionId,
      authStore.userId,
      supplementaryMsg || undefined,
    )

    subscribeTaskEvents(chatStore.executionId)
  } catch (e: any) {
    chatStore.isStreaming = false
    chatStore.setTaskStatus('interrupted')
    ElMessage.error(e?.message || '恢复任务失败')
  }
}

async function handleAbandonTask() {
  if (!chatStore.executionId) return
  try {
    await agentApi.cancelTask(chatStore.executionId, authStore.userId, true)
    chatStore.isStreaming = false
    chatStore.setTaskStatus('cancelled')
    chatStore.executionId = ''
  } catch (e: any) {
    ElMessage.error(e?.message || '放弃任务失败')
  }
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
  taskFinalResult.value = null
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
  let data: any
  try {
    data = await sessionApi.getHistory(sessionId)
  } catch (e: any) {
    // 会话已过期或不存在，清除无效 session_id 并重置状态
    chatStore.setSessionId('')
    sessionStorage.removeItem('current_session_id')
    localStorage.removeItem('current_session_id')
    throw e
  }
  const historyMessages = data.messages || []
  chatStore.loadHistory(historyMessages)
  try {
    const taskStatus = await agentApi.getTaskStatusBySession(sessionId)
    if (taskStatus && taskStatus.execution_id) {
      chatStore.setExecutionId(taskStatus.execution_id)
      chatStore.updateTaskSteps(taskStatus.steps || [], taskStatus.total_steps)
      if (taskStatus.status === 'paused' || taskStatus.steps?.some((s: any) => s.status === 'waiting_confirm')) {
        chatStore.setTaskStatus('paused')
      } else {
        chatStore.setTaskStatus(taskStatus.status || '')
      }
      if (taskStatus.status === 'running' || taskStatus.status === 'paused') {
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
    taskFinalResult.value = null
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
      const stepData = data.data || data
      if (stepData.step_name) {
        runningStepInfo.value = null
      }
    } catch { /* ignore */ }
    refreshTaskStatus()
  })

  es.addEventListener('step_failed', (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data)
      const stepData = data.data || data
      if (stepData.step_name) {
        runningStepInfo.value = null
      }
    } catch { /* ignore */ }
    refreshTaskStatus()
  })

  es.addEventListener('task_completed', (_e: MessageEvent) => {
    chatStore.setTaskStatus('completed')
    chatStore.isStreaming = false
    chatStore.setWaitingConfirm(false)
    runningStepInfo.value = null
    const lastMsg = chatStore.messages.findLast((m) => m.role === 'assistant' && m.streaming)
    if (lastMsg) {
      if (!lastMsg.content.trim()) {
        chatStore.appendToStreamingMessage(lastMsg.id, '任务已执行完成，请查看上方任务进度面板了解详细结果。')
      }
      chatStore.finalizeStreamingMessage(lastMsg.id)
    } else {
      const emptyMsg = chatStore.messages.findLast((m) => m.role === 'assistant')
      if (emptyMsg && !emptyMsg.content.trim()) {
        chatStore.appendToStreamingMessage(emptyMsg.id, '任务已执行完成，请查看上方任务进度面板了解详细结果。')
      }
    }
    // 任务完成后调接口获取最终结果，作为独立AI回复消息显示，并在活动流末尾添加完成标记
    fetchAndDisplayTaskResult()
    refreshTaskStatus()
    closeSSE()
  })

  es.addEventListener('task_paused', (_e: MessageEvent) => {
    chatStore.setTaskStatus('paused')
    runningStepInfo.value = null
    refreshTaskStatus()
  })

  es.addEventListener('task_resumed', (_e: MessageEvent) => {
    chatStore.setTaskStatus('running')
    chatStore.isStreaming = true
    const waitingAct = chatStore.taskActivities.findLast(
      (a) => a.status === 'waiting_confirm',
    )
    if (waitingAct) {
      waitingAct.status = 'completed'
    }
    refreshTaskStatus()
  })

  es.addEventListener('task_step_start', (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data)
      const stepData = data.data || data
      if (stepData.step_name) {
        runningStepInfo.value = {
          step_index: stepData.step_index || 0,
          step_name: stepData.step_name,
          agent_name: stepData.agent_name || '',
          total_steps: stepData.total_steps || 0,
        }
      }
    } catch { /* ignore */ }
    refreshTaskStatus()
  })

  es.addEventListener('task_step_complete', (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data)
      const stepData = data.data || data
      if (stepData.step_name) {
        runningStepInfo.value = null
      }
    } catch { /* ignore */ }
    refreshTaskStatus()
  })

  es.addEventListener('task_interrupted', (_e: MessageEvent) => {
    chatStore.setTaskStatus('interrupted')
    chatStore.isStreaming = false
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
      // 检查是否有未处理的待确认步骤
      const hasUnhandledConfirm = status.steps?.some(
        (s: any) => s.status === 'waiting_confirm' && s.confirm_id && !chatStore.resolvedConfirmIds.has(s.confirm_id),
      )
      if (status.status === 'paused' && hasUnhandledConfirm) {
        chatStore.setTaskStatus('paused')
      } else if (status.status === 'completed' || status.status === 'failed' || status.status === 'cancelled') {
        chatStore.setTaskStatus(status.status)
      } else if (status.status === 'interrupted') {
        chatStore.setTaskStatus('interrupted')
        chatStore.isStreaming = false
      } else {
        // running 或其他状态，保持当前状态
        if (chatStore.taskStatus !== 'running') {
          chatStore.setTaskStatus('running')
        }
      }
    }
  } catch { /* ignore */ }
}

async function fetchAndDisplayTaskResult() {
  if (!chatStore.executionId) return
  try {
    const status = await agentApi.getTaskStatus(chatStore.executionId)
    if (!status || !status.steps) return

    // 从步骤中查找最终结果（裁判裁决、汇总等关键步骤的输出）
    const resultSteps = status.steps.filter(
      (s: any) => s.result && s.status === 'completed',
    )

    let finalResult = ''
    let resultAgentName = ''

    for (const step of resultSteps) {
      const stepResult = (step as any).result as string
      if (!stepResult) continue
      const isAggregateStep = step.step_type === 'aggregate' || step.step_name?.includes('裁决') || step.step_name?.includes('汇总') || step.step_name?.includes('总结')
      const isLastStep = step.step_index === status.total_steps
      if (isAggregateStep || isLastStep) {
        finalResult = stepResult
        resultAgentName = step.agent_name || ''
        break
      }
    }

    // 将最终结果存储到 taskFinalResult，在任务看板下方独立展示
    if (finalResult) {
      taskFinalResult.value = {
        content: finalResult,
        agentName: resultAgentName,
      }
    }

    // 在活动流末尾添加"任务执行完成"标记，确保它在最后显示
    const hasCompletedActivity = chatStore.taskActivities.some(
      (a) => a.type === 'handoff' && a.content === '任务执行完成',
    )
    if (!hasCompletedActivity) {
      chatStore.addTaskActivity({
        type: 'handoff',
        agentName: '',
        content: '任务执行完成',
        status: 'completed',
      })
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

    // 确认后立即更新前端状态，不等待 SSE 事件
    if (decision === 'continue' || decision === 'skip') {
      chatStore.setTaskStatus('running')
      runningStepInfo.value = null
      const waitingAct = chatStore.taskActivities.findLast(
        (a) => a.status === 'waiting_confirm',
      )
      if (waitingAct) {
        waitingAct.status = 'completed'
      }
      // 向最后一条消息追加确认信息
      const lastMsg = chatStore.messages.findLast((m) => m.role === 'assistant')
      if (lastMsg) {
        const confirmText = decision === 'continue' ? '\n> [已确认] 继续执行...\n' : '\n> [已跳过] 跳过此步骤...\n'
        chatStore.appendToStreamingMessage(lastMsg.id, confirmText)
        scrollToBottom()
      }
    } else if (decision === 'cancel') {
      chatStore.setTaskStatus('cancelled')
      const lastMsg = chatStore.messages.findLast((m) => m.role === 'assistant')
      if (lastMsg) {
        chatStore.appendToStreamingMessage(lastMsg.id, '\n> [已取消] 任务已取消\n')
      }
    }

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
        // 会话已过期，清除无效状态
        chatStore.setSessionId('')
        sessionStorage.removeItem('current_session_id')
        localStorage.removeItem('current_session_id')
      }
    }
    // 仅运行中的任务才订阅SSE，已完成/已暂停/已中断的不需要
    if (chatStore.taskStatus === 'running') {
      subscribeTaskEvents(chatStore.executionId)
    } else if (chatStore.taskStatus === 'completed') {
      // 已完成的任务，调接口获取最终结果并展示到任务看板
      fetchAndDisplayTaskResult()
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
      // 会话恢复失败（已过期或不存在），清除无效状态，用户可正常开始新对话
      chatStore.setSessionId('')
      sessionStorage.removeItem('current_session_id')
      localStorage.removeItem('current_session_id')
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
  margin-bottom: 24px;
  padding: 0 4px;
  animation: slideIn 0.3s ease-out;
}

@keyframes slideIn {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}

.message-row.user {
  display: flex;
  justify-content: flex-end;
}

.message-row.assistant {
  display: flex;
  justify-content: flex-start;
}

/* 用户消息：右侧气泡 */
.user-content {
  max-width: 65%;
  min-width: 0;
}

.msg-bubble.user {
  background: var(--color-primary);
  color: white;
  padding: 10px 16px;
  border-radius: 16px 16px 4px 16px;
  font-size: 14px;
  line-height: 1.65;
  word-wrap: break-word;
}

.msg-bubble.user :deep(code) {
  background: rgba(255,255,255,0.15);
  font-family: var(--font-mono);
  font-size: 13px;
  padding: 2px 6px;
  border-radius: 4px;
}

/* AI 消息：左侧 Trae 风格，无边框气泡 */
.assistant-content {
  max-width: 85%;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.assistant-reply {
  font-size: 14px;
  line-height: 1.75;
  color: var(--color-text);
  word-wrap: break-word;
}

.assistant-reply :deep(p) {
  margin: 4px 0;
}

.assistant-reply :deep(p:first-child) {
  margin-top: 0;
}

.assistant-reply :deep(p:last-child) {
  margin-bottom: 0;
}

.assistant-reply :deep(code) {
  font-family: var(--font-mono);
  font-size: 13px;
  background: rgba(0,0,0,0.06);
  padding: 2px 6px;
  border-radius: 4px;
}

.assistant-reply :deep(pre) {
  margin: 8px 0;
  padding: 12px;
  background: #1e293b;
  color: #e2e8f0;
  border-radius: var(--radius-md);
  overflow-x: auto;
}

.assistant-reply :deep(pre code) {
  background: none;
  padding: 0;
  color: inherit;
}

/* 任务看板卡片 - 穿插显示 */
.task-board-card {
  border: 1px solid var(--color-border-light);
  border-radius: 10px;
  padding: 10px 12px;
  background: var(--color-bg);
  animation: boardFadeIn 0.3s ease-out;
}

@keyframes boardFadeIn {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}

.task-board-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
}

.board-icon {
  color: var(--color-text-tertiary);
  flex-shrink: 0;
}

.board-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text);
}

.board-progress {
  font-size: 12px;
  color: var(--color-text-secondary);
  font-weight: 500;
}

.board-steps {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.board-step {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  border-radius: 6px;
  transition: background var(--transition-fast);
}

.board-step.completed {
  background: transparent;
}

.board-step.running {
  background: #e6f7ff;
}

.board-step.failed {
  background: #fff1f0;
}

.board-step.waiting_confirm {
  background: #fff7e6;
}

.board-step.pending {
  opacity: 0.55;
}

.board-step-indicator {
  width: 12px;
  height: 12px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

.step-icon-done {
  color: #52c41a;
}

.step-icon-fail {
  color: #f5222d;
}

.step-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-border);
  display: block;
}

.board-step.pending .step-dot {
  background: #d9d9d9;
}

.step-spinner {
  display: block;
  width: 12px;
  height: 12px;
  border: 2px solid #e6f7ff;
  border-top-color: #1890ff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.board-step-name {
  font-size: 12px;
  font-weight: 500;
  color: var(--color-text);
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.board-step.pending .board-step-name {
  color: var(--color-text-tertiary);
}

.board-step-agent {
  font-size: 10px;
  color: var(--color-text-tertiary);
  background: var(--color-bg-elevated);
  padding: 1px 5px;
  border-radius: 3px;
  border: 1px solid var(--color-border-light);
  white-space: nowrap;
  flex-shrink: 0;
}

/* 穿插文字内容 */
.timeline-text-item {
  padding: 2px 0;
  animation: textFadeIn 0.25s ease-out;
}

@keyframes textFadeIn {
  from { opacity: 0; transform: translateX(-4px); }
  to { opacity: 1; transform: translateX(0); }
}

.text-agent {
  font-size: 10px;
  font-weight: 600;
  color: var(--color-primary);
  background: var(--color-primary-bg);
  padding: 1px 5px;
  border-radius: 3px;
  white-space: nowrap;
  margin-right: 4px;
}

/* 思考内容 - 内联 */
.thought-inline {
  display: flex;
  align-items: flex-start;
  gap: 5px;
  font-size: 12px;
  color: var(--color-text-secondary);
  padding: 4px 8px;
  border-radius: 6px;
  background: rgba(0,0,0,0.02);
}

.thought-icon {
  color: var(--color-text-tertiary);
  flex-shrink: 0;
  margin-top: 1px;
}

.thought-content {
  flex: 1;
  min-width: 0;
  line-height: 1.5;
}

/* 推理类型标签 */
.reasoning-type-badge {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 500;
  line-height: 1.4;
  white-space: nowrap;
}
.reasoning-type-badge.reasoning-intent {
  background: #e6f7ff;
  color: #1890ff;
}
.reasoning-type-badge.reasoning-planning {
  background: #f6ffed;
  color: #52c41a;
}
.reasoning-type-badge.reasoning-review {
  background: #fff7e6;
  color: #fa8c16;
}
.reasoning-type-badge.reasoning-tool_selection {
  background: #f9f0ff;
  color: #722ed1;
}
.reasoning-type-badge.reasoning-execution {
  background: #f0f5ff;
  color: #2f54eb;
}

/* 推理折叠展开按钮 */
.reasoning-toggle-btn {
  padding: 1px 8px;
  border: 1px solid #d9d9d9;
  border-radius: 4px;
  background: #fff;
  color: var(--color-text-secondary);
  font-size: 11px;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.2s;
}
.reasoning-toggle-btn:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

/* 推理详情折叠区域 */
.reasoning-detail {
  margin-top: 6px;
  padding: 8px 12px;
  background: #fafafa;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
  max-height: 300px;
  overflow-y: auto;
}
.reasoning-pre {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  color: #595959;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;
}

/* 工具调用 - 内联 */
.tool-inline {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--color-text-secondary);
  padding: 3px 8px;
  border-radius: 6px;
  background: #f0f5ff;
}

.tool-icon {
  color: #1890ff;
  flex-shrink: 0;
}

.tool-content {
  flex: 1;
  min-width: 0;
}

/* 工具结果 - 内联 */
.result-inline {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: #52c41a;
  padding: 3px 8px;
  border-radius: 6px;
  background: #f6ffed;
}

.result-inline.result-failed {
  color: #f5222d;
  background: #fff1f0;
}

.result-icon-ok {
  color: #52c41a;
  flex-shrink: 0;
}

.result-icon-fail {
  color: #f5222d;
  flex-shrink: 0;
}

.result-content {
  flex: 1;
  min-width: 0;
}

/* Agent 切换 - 内联 */
.handoff-inline {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--color-text-secondary);
  padding: 3px 8px;
  border-radius: 6px;
  background: rgba(0,0,0,0.02);
}

.handoff-icon {
  color: #722ed1;
  flex-shrink: 0;
}

.handoff-content {
  flex: 1;
  min-width: 0;
}

/* 通用文字内容 */
.generic-inline {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--color-text-secondary);
  padding: 3px 8px;
}

.generic-content {
  flex: 1;
  min-width: 0;
}

/* 任务状态标签 */
.task-status-badge {
  font-size: 11px;
  padding: 1px 8px;
  border-radius: 10px;
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

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* 任务最终结果块 */
.task-result-block {
  border: 1px solid #b7eb8f;
  border-radius: 10px;
  padding: 12px;
  background: #f6ffed;
}

.task-result-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
  color: #52c41a;
}

.task-result-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--color-text);
}

.task-result-agent {
  font-size: 11px;
  color: var(--color-text-tertiary);
  background: white;
  padding: 1px 6px;
  border-radius: 4px;
  border: 1px solid var(--color-border-light);
}

.task-result-content {
  font-size: 14px;
  line-height: 1.7;
  color: var(--color-text);
  max-height: 500px;
  overflow-y: auto;
  word-break: break-word;
}

.task-result-content :deep(h1),
.task-result-content :deep(h2),
.task-result-content :deep(h3) {
  margin-top: 12px;
  margin-bottom: 6px;
  font-size: 15px;
  font-weight: 700;
}

.task-result-content :deep(p) {
  margin: 4px 0;
}

.task-result-content :deep(ul),
.task-result-content :deep(ol) {
  padding-left: 20px;
  margin: 4px 0;
}

/* 人工确认卡片 */
.confirm-inline-card {
  border: 1px solid #ffd591;
  background: #fffbe6;
  border-radius: 10px;
  padding: 12px;
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

.btn-stop {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: var(--radius-md);
  background: #e74c3c;
  color: white;
  transition: all var(--transition-fast);
  flex-shrink: 0;
  cursor: pointer;
}

.btn-stop:hover {
  background: #c0392b;
  transform: scale(1.05);
}

.btn-resume-send {
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 48px;
  height: 36px;
  padding: 0 12px;
  border-radius: var(--radius-md);
  background: #27ae60;
  color: white;
  font-size: 13px;
  font-weight: 600;
  transition: all var(--transition-fast);
  flex-shrink: 0;
  cursor: pointer;
}

.btn-resume-send:hover {
  background: #219a52;
  transform: scale(1.05);
}

.task-action-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: #fef9e7;
  border: 1px solid #f0d96b;
  border-radius: var(--radius-md) var(--radius-md) 0 0;
  margin-bottom: -1px;
}

.task-action-hint {
  font-size: 13px;
  color: #8a6d3b;
}

.task-action-buttons {
  display: flex;
  gap: 8px;
}

.btn-task-action {
  padding: 4px 14px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all var(--transition-fast);
  border: none;
}

.btn-resume {
  background: #27ae60;
  color: white;
}

.btn-resume:hover {
  background: #219a52;
}

.btn-abandon {
  background: #e74c3c;
  color: white;
}

.btn-abandon:hover {
  background: #c0392b;
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

  .user-content {
    max-width: 80%;
  }

  .assistant-content {
    max-width: 95%;
  }
}
</style>

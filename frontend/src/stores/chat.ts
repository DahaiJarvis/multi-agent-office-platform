import { defineStore } from 'pinia'
import { ref } from 'vue'
import { agentApi, type TaskStepStatus, type ConfirmOption } from '../api/agent'

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp: number
  agentName?: string
  intent?: string
  collaborationMode?: string
  feedback?: 'thumbs_up' | 'thumbs_down' | null
  streaming?: boolean
  executionId?: string
}

let _msgSeq = 0

function _nextMsgId(prefix: string = 'msg'): string {
  _msgSeq += 1
  return `${prefix}-${Date.now()}-${_msgSeq}`
}

export const useChatStore = defineStore('chat', () => {
  const sessionId = ref('')
  const messages = ref<Message[]>([])
  const isStreaming = ref(false)
  const currentAgent = ref('')

  // 任务执行状态
  const executionId = ref('')
  const taskSteps = ref<TaskStepStatus[]>([])
  const waitingConfirm = ref(false)
  const confirmInfo = ref<{
    confirmId: string
    confirmType: string
    confirmReason: string
    options: ConfirmOption[]
    stepIndex: number
  } | null>(null)
  const taskStatus = ref('')

  function addUserMessage(content: string) {
    messages.value.push({
      id: _nextMsgId(),
      role: 'user',
      content,
      timestamp: Date.now(),
    })
  }

  function addAssistantMessage(content: string, meta?: { agentName?: string; intent?: string; mode?: string; executionId?: string }) {
    messages.value.push({
      id: _nextMsgId(),
      role: 'assistant',
      content,
      timestamp: Date.now(),
      agentName: meta?.agentName,
      intent: meta?.intent,
      collaborationMode: meta?.mode,
      executionId: meta?.executionId,
      feedback: null,
    })
  }

  function addStreamingMessage(): string {
    const id = _nextMsgId('stream')
    messages.value.push({
      id,
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
      streaming: true,
      feedback: null,
    })
    return id
  }

  function appendToStreamingMessage(id: string, chunk: string) {
    const msg = messages.value.find((m) => m.id === id)
    if (msg) {
      msg.content += chunk
    }
  }

  function finalizeStreamingMessage(id: string, meta?: { agentName?: string; intent?: string; mode?: string; executionId?: string }) {
    const msg = messages.value.find((m) => m.id === id)
    if (msg) {
      msg.streaming = false
      msg.agentName = meta?.agentName
      msg.intent = meta?.intent
      msg.collaborationMode = meta?.mode
      msg.executionId = meta?.executionId
    }
  }

  function setMessageFeedback(messageId: string, feedback: 'thumbs_up' | 'thumbs_down' | null) {
    const msg = messages.value.find((m) => m.id === messageId)
    if (msg) {
      msg.feedback = feedback
    }
  }

  function setSessionId(id: string) {
    sessionId.value = id
  }

  function setExecutionId(id: string) {
    executionId.value = id
    if (id) {
      sessionStorage.setItem('current_execution_id', id)
    } else {
      sessionStorage.removeItem('current_execution_id')
    }
  }

  function updateTaskSteps(steps: TaskStepStatus[]) {
    taskSteps.value = steps
    // 检查是否有人工确认步骤
    const confirmStep = steps.find(
      (s) => s.status === 'waiting_confirm' && s.confirm_id,
    )
    if (confirmStep && confirmStep.confirm_id) {
      waitingConfirm.value = true
      confirmInfo.value = {
        confirmId: confirmStep.confirm_id,
        confirmType: confirmStep.confirm_type || 'sensitive_action',
        confirmReason: confirmStep.confirm_reason || '',
        options: confirmStep.options || [],
        stepIndex: confirmStep.step_index,
      }
    } else {
      waitingConfirm.value = false
      confirmInfo.value = null
    }
  }

  function setWaitingConfirm(val: boolean) {
    waitingConfirm.value = val
    if (!val) {
      confirmInfo.value = null
    }
  }

  function setTaskStatus(status: string) {
    taskStatus.value = status
  }

  function clearChat() {
    sessionId.value = ''
    messages.value = []
    currentAgent.value = ''
    executionId.value = ''
    taskSteps.value = []
    waitingConfirm.value = false
    confirmInfo.value = null
    taskStatus.value = ''
    sessionStorage.removeItem('current_execution_id')
  }

  function loadHistory(historyMessages: any[]) {
    messages.value = historyMessages.map((m, i) => ({
      id: `msg-hist-${i}`,
      role: m.role,
      content: m.content,
      timestamp: new Date(m.timestamp || m.created_at || Date.now()).getTime(),
      agentName: m.metadata?.agent,
      intent: m.metadata?.intent,
      collaborationMode: m.metadata?.collaboration_mode,
      feedback: null,
    }))
  }

  async function recoverTaskStatus() {
    const savedExecutionId = sessionStorage.getItem('current_execution_id')
    if (!savedExecutionId) return false

    try {
      const status = await agentApi.getTaskStatus(savedExecutionId)
      if (status) {
        executionId.value = savedExecutionId
        taskSteps.value = status.steps || []
        taskStatus.value = status.status || ''

        if (status.status === 'paused') {
          waitingConfirm.value = true
          const confirmStep = status.steps?.find(
            (s: TaskStepStatus) => s.status === 'waiting_confirm' && s.confirm_id,
          )
          if (confirmStep && confirmStep.confirm_id) {
            confirmInfo.value = {
              confirmId: confirmStep.confirm_id,
              confirmType: confirmStep.confirm_type || 'sensitive_action',
              confirmReason: confirmStep.confirm_reason || '',
              options: confirmStep.options || [],
              stepIndex: confirmStep.step_index,
            }
          }
        }

        if (status.status === 'interrupted') {
          taskStatus.value = 'interrupted'
        }

        return true
      }
    } catch {
      // 恢复失败，清除保存的ID
      sessionStorage.removeItem('current_execution_id')
    }

    return false
  }

  return {
    sessionId,
    messages,
    isStreaming,
    currentAgent,
    executionId,
    taskSteps,
    waitingConfirm,
    confirmInfo,
    taskStatus,
    addUserMessage,
    addAssistantMessage,
    addStreamingMessage,
    appendToStreamingMessage,
    finalizeStreamingMessage,
    setMessageFeedback,
    setSessionId,
    setExecutionId,
    updateTaskSteps,
    setWaitingConfirm,
    setTaskStatus,
    clearChat,
    loadHistory,
    recoverTaskStatus,
  }
})

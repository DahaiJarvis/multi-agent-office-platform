import { defineStore } from 'pinia'
import { ref } from 'vue'
import { agentApi, type TaskStepStatus, type ConfirmOption } from '../api/agent'

export interface TaskActivity {
  id: string
  type: 'intent' | 'thought' | 'tool_call' | 'tool_result' | 'step_start' | 'step_done' | 'handoff' | 'result'
  agentName: string
  content: string
  timestamp: number
  status?: string
  detail?: string
}

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
  const totalSteps = ref(0)
  const waitingConfirm = ref(false)
  const confirmInfo = ref<{
    confirmId: string
    confirmType: string
    confirmReason: string
    options: ConfirmOption[]
    stepIndex: number
  } | null>(null)
  const taskStatus = ref('')

  // 任务实时活动记录（来自 SSE 流事件）
  const taskActivities = ref<TaskActivity[]>([])
  let _activitySeq = 0

  // 已确认的 confirm_id 集合，防止重复触发确认弹窗
  const resolvedConfirmIds = new Set<string>()

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
    if (id) {
      sessionStorage.setItem('current_session_id', id)
      localStorage.setItem('current_session_id', id)
    } else {
      sessionStorage.removeItem('current_session_id')
      localStorage.removeItem('current_session_id')
    }
  }

  function setExecutionId(id: string) {
    executionId.value = id
    if (id) {
      sessionStorage.setItem('current_execution_id', id)
      localStorage.setItem('current_execution_id', id)
    } else {
      sessionStorage.removeItem('current_execution_id')
      localStorage.removeItem('current_execution_id')
    }
  }

  function updateTaskSteps(steps: TaskStepStatus[], total?: number) {
    taskSteps.value = steps
    if (total !== undefined && total > 0) {
      totalSteps.value = total
    }
    // 检查是否有新的人工确认步骤
    const confirmStep = steps.find(
      (s) => s.status === 'waiting_confirm' && s.confirm_id,
    )
    if (confirmStep && confirmStep.confirm_id) {
      // 如果该确认ID已被用户处理过，不重复触发
      if (resolvedConfirmIds.has(confirmStep.confirm_id)) {
        return
      }
      // 如果是同一个确认ID，不重复触发（用户刚确认过的）
      const isSameConfirm = confirmInfo.value && confirmInfo.value.confirmId === confirmStep.confirm_id
      if (!isSameConfirm) {
        waitingConfirm.value = true
        confirmInfo.value = {
          confirmId: confirmStep.confirm_id,
          confirmType: confirmStep.confirm_type || 'sensitive_action',
          confirmReason: confirmStep.confirm_reason || '',
          options: confirmStep.options || [],
          stepIndex: confirmStep.step_index,
        }
      }
    } else if (steps.length > 0) {
      // 步骤列表非空但没有待确认步骤，清除确认状态
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

  function addTaskActivity(activity: Omit<TaskActivity, 'id' | 'timestamp'>) {
    _activitySeq += 1
    taskActivities.value.push({
      id: `act-${Date.now()}-${_activitySeq}`,
      timestamp: Date.now(),
      ...activity,
    })
  }

  function clearTaskActivities() {
    taskActivities.value = []
    _activitySeq = 0
  }

  function clearChat() {
    sessionId.value = ''
    messages.value = []
    currentAgent.value = ''
    executionId.value = ''
    taskSteps.value = []
    totalSteps.value = 0
    waitingConfirm.value = false
    confirmInfo.value = null
    taskStatus.value = ''
    taskActivities.value = []
    _activitySeq = 0
    resolvedConfirmIds.clear()
    sessionStorage.removeItem('current_execution_id')
    sessionStorage.removeItem('current_session_id')
    localStorage.removeItem('current_execution_id')
    localStorage.removeItem('current_session_id')
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

  async function recoverTaskStatus(): Promise<{ recovered: boolean; sessionId?: string }> {
    const savedExecutionId = sessionStorage.getItem('current_execution_id') || localStorage.getItem('current_execution_id')
    if (!savedExecutionId) return { recovered: false }

    try {
      const status = await agentApi.getTaskStatus(savedExecutionId)
      if (status) {
        executionId.value = savedExecutionId
        taskSteps.value = status.steps || []
        totalSteps.value = status.total_steps || 0
        taskStatus.value = status.status || ''

        // 从任务状态中恢复 sessionId
        if (status.session_id) {
          sessionId.value = status.session_id
          sessionStorage.setItem('current_session_id', status.session_id)
          localStorage.setItem('current_session_id', status.session_id)
        }

        if (status.status === 'paused' || status.steps?.some((s: TaskStepStatus) => s.status === 'waiting_confirm')) {
          taskStatus.value = 'paused'
          waitingConfirm.value = true
          const confirmStep = status.steps?.find(
            (s: TaskStepStatus) => s.status === 'waiting_confirm' && s.confirm_id,
          )
          if (confirmStep && confirmStep.confirm_id) {
            if (!resolvedConfirmIds.has(confirmStep.confirm_id)) {
              confirmInfo.value = {
                confirmId: confirmStep.confirm_id,
                confirmType: confirmStep.confirm_type || 'sensitive_action',
                confirmReason: confirmStep.confirm_reason || '',
                options: confirmStep.options || [],
                stepIndex: confirmStep.step_index,
              }
            }
          }
        }

        if (status.status === 'interrupted') {
          taskStatus.value = 'interrupted'
        }

        return { recovered: true, sessionId: status.session_id }
      }
    } catch {
      // 恢复失败，清除保存的ID
      sessionStorage.removeItem('current_execution_id')
      sessionStorage.removeItem('current_session_id')
      localStorage.removeItem('current_execution_id')
      localStorage.removeItem('current_session_id')
    }

    return { recovered: false }
  }

  return {
    sessionId,
    messages,
    isStreaming,
    currentAgent,
    executionId,
    taskSteps,
    totalSteps,
    waitingConfirm,
    confirmInfo,
    taskStatus,
    resolvedConfirmIds,
    taskActivities,
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
    addTaskActivity,
    clearTaskActivities,
    clearChat,
    loadHistory,
    recoverTaskStatus,
  }
})

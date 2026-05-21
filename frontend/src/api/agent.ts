import http from './request'

export interface ChatParams {
  message: string
  session_id?: string
  user_id: string
  channel?: string
  knowledge_base_id?: string
}

export interface ChatResult {
  session_id: string
  message: string
  agent_name: string
  intent?: string
  collaboration_mode?: string
  execution_id?: string
}

export interface FeedbackParams {
  session_id: string
  message_index: number
  feedback_type: 'thumbs_up' | 'thumbs_down'
  comment?: string
  agent_name?: string
  intent?: string
}

export interface FeedbackStats {
  total_feedback: number
  thumbs_up: number
  thumbs_down: number
  satisfaction_rate: number
  by_agent?: Record<string, { thumbs_up: number; thumbs_down: number }>
}

export interface TaskStepStatus {
  step_index: number
  step_name: string
  step_type: string
  agent_name: string
  status: string
  error: string
  fallback_used: string
  confirm_id?: string
  confirm_type?: string
  confirm_reason?: string
  options?: ConfirmOption[]
  result?: string
}

export interface ConfirmOption {
  label: string
  value: string
  description: string
}

export interface TaskExecutionStatus {
  execution_id: string
  session_id: string
  status: string
  current_step: number
  total_steps: number
  failure_policy: string
  error: string
  steps: TaskStepStatus[]
  created_at: number
  updated_at: number
}

export interface PendingConfirm {
  confirm_id: string
  execution_id: string
  step_index: number
  session_id: string
  user_id: string
  confirm_type: string
  reason: string
  options: ConfirmOption[]
  status: string
  agent_name: string
  created_at: number
  expires_at: number
}

export const agentApi = {
  chat(params: ChatParams) {
    return http.post<ChatResult>('/agent/chat', params)
  },

  chatStream(params: ChatParams): EventSource | null {
    const API_BASE = import.meta.env.VITE_API_BASE || '/api/v1'
    const token = localStorage.getItem('access_token')

    const eventSource = new EventSource(
      `${API_BASE}/agent/chat/stream?` +
      `message=${encodeURIComponent(params.message)}&` +
      `user_id=${encodeURIComponent(params.user_id)}&` +
      (params.session_id ? `session_id=${encodeURIComponent(params.session_id)}&` : '') +
      `token=${token}`,
    )

    return eventSource
  },

  chatStreamFetch(
    params: ChatParams,
    onChunk: (data: any) => void,
    onError: (err: any) => void,
    onDone: () => void,
  ) {
    const API_BASE = import.meta.env.VITE_API_BASE || '/api/v1'
    const token = localStorage.getItem('access_token')

    fetch(`${API_BASE}/agent/chat/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(params),
    })
      .then(async (response) => {
        if (!response.ok) {
          onError(new Error(`HTTP ${response.status}`))
          return
        }
        const reader = response.body?.getReader()
        if (!reader) return

        const decoder = new TextDecoder()
        let buffer = ''
        let currentEvent = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''

          for (const line of lines) {
            // 解析 SSE event 行，记录当前事件类型
            if (line.startsWith('event: ')) {
              currentEvent = line.slice(7).trim()
              continue
            }

            if (line.startsWith('data: ')) {
              try {
                const payload = JSON.parse(line.slice(6))
                // 将 SSE event 类型注入 payload，便于上层区分处理
                if (currentEvent) {
                  payload._sseEvent = currentEvent
                }
                // 错误事件特殊处理
                if (currentEvent === 'error' || payload.event === 'error') {
                  onError(payload)
                  continue
                }
                onChunk(payload)
              } catch {
                // skip malformed
              }
              // data 行处理完毕后重置 event 类型
              currentEvent = ''
            }
          }
        }
        onDone()
      })
      .catch(onError)
  },

  submitFeedback(params: FeedbackParams) {
    return http.post('/agent/feedback', params)
  },

  getFeedbackStats(date?: string) {
    return http.get<FeedbackStats>('/agent/feedback/stats', { params: { date } })
  },

  getAgentFeedbackStats(agentName: string, date?: string) {
    return http.get<FeedbackStats>(`/agent/feedback/stats/${agentName}`, { params: { date } })
  },

  getTaskStatus(executionId: string) {
    return http.get<TaskExecutionStatus>(`/agent/task/${executionId}`)
  },

  getTaskStatusBySession(sessionId: string) {
    return http.get<TaskExecutionStatus>(`/agent/task/session/${sessionId}`)
  },

  resumeTask(executionId: string, sessionId: string, userId: string, supplementaryMessage?: string) {
    return http.post<TaskExecutionStatus>(`/agent/task/resume`, {
      execution_id: executionId,
      session_id: sessionId,
      user_id: userId,
      supplementary_message: supplementaryMessage || null,
    })
  },

  retryStep(executionId: string, stepIndex: number, userId: string, agentName?: string) {
    return http.post<TaskExecutionStatus>(`/agent/task/retry`, {
      execution_id: executionId,
      step_index: stepIndex,
      user_id: userId,
      agent_name: agentName,
    })
  },

  confirmTask(confirmId: string, decision: string, comment?: string, userId?: string, agentName?: string, executionId?: string, stepIndex?: number) {
    return http.post(`/agent/task/confirm/${confirmId}`, {
      execution_id: executionId || '',
      step_index: stepIndex ?? 0,
      decision,
      comment: comment || '',
      user_id: userId || '',
      agent_name: agentName,
    })
  },

  cancelTask(executionId: string, userId: string, force: boolean = false) {
    return http.post(`/agent/task/cancel`, {
      execution_id: executionId,
      user_id: userId,
      force,
    })
  },

  getPendingConfirms(userId: string) {
    return http.get<{ user_id: string; pending_count: number; confirms: PendingConfirm[] }>(
      `/agent/task/confirms/${userId}`,
    )
  },

  subscribeTaskEvents(executionId: string): EventSource | null {
    const API_BASE = import.meta.env.VITE_API_BASE || '/api/v1'
    const token = localStorage.getItem('access_token')

    const eventSource = new EventSource(
      `${API_BASE}/agent/task/events/${executionId}?token=${token}`,
    )
    return eventSource
  },
}

import http from './request'

export interface ChatParams {
  message: string
  session_id?: string
  user_id: string
  channel?: string
}

export interface ChatResult {
  session_id: string
  message: string
  agent_name: string
  intent?: string
  collaboration_mode?: string
}

export interface FeedbackParams {
  session_id: string
  message_index: number
  feedback_type: 'thumbs_up' | 'thumbs_down'
  comment?: string
  agent_name?: string
  intent?: string
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

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const payload = JSON.parse(line.slice(6))
                onChunk(payload)
              } catch {
                // skip malformed
              }
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
    return http.get('/agent/feedback/stats', { params: { date } })
  },

  getAgentFeedbackStats(agentName: string, date?: string) {
    return http.get(`/agent/feedback/stats/${agentName}`, { params: { date } })
  },
}

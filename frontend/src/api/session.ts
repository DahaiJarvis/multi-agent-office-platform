import http from './request'

export interface SessionInfo {
  session_id: string
  user_id: string
  channel: string
  created_at: string
  updated_at: string
  message_count: number
  active_agents: string[]
}

export interface CreateSessionParams {
  user_id: string
  channel?: string
}

export const sessionApi = {
  create(params: CreateSessionParams) {
    return http.post<SessionInfo>('/session/create', params)
  },

  get(sessionId: string) {
    return http.get<SessionInfo>(`/session/${sessionId}`)
  },

  getHistory(sessionId: string, limit = 50) {
    return http.get(`/session/${sessionId}/history`, { params: { limit } })
  },

  archive(sessionId: string) {
    return http.post(`/session/${sessionId}/archive`)
  },

  listUserSessions(userId: string, limit = 20, offset = 0) {
    return http.get(`/session/user/${userId}/history`, { params: { limit, offset } })
  },

  delete(sessionId: string) {
    return http.delete(`/session/${sessionId}`)
  },
}

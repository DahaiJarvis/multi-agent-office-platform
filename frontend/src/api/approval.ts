import http from './request'

export interface ApprovalItem {
  approval_id: string
  session_id: string
  user_id: string
  agent_name: string
  tool_name: string
  reason: string
  status: string
  approver: string
  approver_role: string
  created_at: number
  resolved_at: number
  expires_at: number
  current_step: number
  total_steps: number
}

export interface ApprovalListResult {
  items: ApprovalItem[]
  total: number
}

export const approvalApi = {
  create(data: {
    session_id?: string
    user_id?: string
    agent_name?: string
    tool_name: string
    tool_input?: Record<string, unknown>
    reason?: string
    approval_chain?: Array<Record<string, unknown>>
    timeout_hours?: number
  }) {
    return http.post<ApprovalItem>('/approval/create', data)
  },

  approve(approvalId: string, approver: string, comment?: string) {
    return http.post<ApprovalItem>(`/approval/${approvalId}/approve`, { approver, comment: comment || '' })
  },

  reject(approvalId: string, approver: string, comment?: string) {
    return http.post<ApprovalItem>(`/approval/${approvalId}/reject`, { approver, comment: comment || '' })
  },

  listPending(params?: { approver?: string; status?: string; limit?: number; offset?: number }) {
    return http.get<ApprovalListResult>('/approval/pending', { params })
  },

  get(approvalId: string) {
    return http.get<ApprovalItem>(`/approval/${approvalId}`)
  },

  cancel(approvalId: string, approver: string, comment?: string) {
    return http.post<ApprovalItem>(`/approval/${approvalId}/cancel`, { approver, comment: comment || '' })
  },

  checkExpired() {
    return http.post<ApprovalListResult>('/approval/check-expired')
  },
}

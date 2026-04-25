import http from './request'

export const adminApi = {
  health() {
    return http.get('/admin/health')
  },

  healthDetail() {
    return http.get('/admin/health/detail')
  },

  mcpStatus() {
    return http.get('/admin/mcp/status')
  },

  failoverStatus() {
    return http.get('/admin/failover/status')
  },

  // 灰度发布
  canaryFlags() {
    return http.get('/admin/canary/flags')
  },

  canaryRollout(featureName: string, percentage: number) {
    return http.post('/admin/canary/rollout', { feature_name: featureName, percentage })
  },

  canaryWhitelist(featureName: string, userIds: string[]) {
    return http.post('/admin/canary/whitelist', { feature_name: featureName, user_ids: userIds })
  },

  canaryToggle(featureName: string, enabled: boolean) {
    return http.post('/admin/canary/toggle', { feature_name: featureName, enabled })
  },

  // 运营指标
  metricsSummary() {
    return http.get('/admin/metrics/summary')
  },

  // Token 预算
  tokenUsage(userId: string) {
    return http.get(`/admin/token/usage/${userId}`)
  },

  tokenBudget(userId: string, sessionId?: string) {
    return http.get(`/admin/token/budget/${userId}`, { params: { session_id: sessionId } })
  },

  // 审计日志
  auditLogs(params?: { event_type?: string; user_id?: string; action?: string; limit?: number; offset?: number }) {
    return http.get('/admin/audit/logs', { params })
  },

  auditFlush() {
    return http.post('/admin/audit/flush')
  },
}

import http from './request'

export interface HealthDetail {
  status: string
  version: string
  timestamp: string
  checks: Record<string, {
    healthy: boolean
    latency_ms: number
    error?: string
  }>
}

export interface MCPService {
  name: string
  status: string
  url: string
  latency_ms?: number
}

export interface MCPStatus {
  services: MCPService[]
}

export interface FailoverStatus {
  enabled: boolean
  primary: string
  secondary?: string
  active: string
  last_failover?: string
}

export interface CanaryFlag {
  name: string
  enabled: boolean
  rollout_percentage: number
  whitelist: string[]
}

export interface CanaryFlagsResult {
  flags: CanaryFlag[]
}

export interface AgentDistribution {
  agent: string
  count: number
}

export interface MetricsSummary {
  total_sessions: number
  total_messages: number
  active_users: number
  agent_distribution: AgentDistribution[]
}

export interface TokenUsageSession {
  session_id: string
  tokens: number
  cost: number
  model?: string
}

export interface TokenUsage {
  total_tokens: number
  total_cost: number
  sessions: TokenUsageSession[]
}

export interface TokenBudget {
  daily_limit: number
  used: number
  remaining: number
  session_limit: number
}

export interface UserItem {
  user_id: string
  roles: string[]
  departments: string[]
  is_active: boolean
}

export interface UserListResult {
  items: UserItem[]
  total: number
}

export interface AuditLogEntry {
  id?: string
  event_type: string
  user_id: string
  action: string
  resource?: string
  detail?: string
  timestamp: string
}

export interface AuditLogsResult {
  logs: AuditLogEntry[]
  total?: number
}

export interface DRStatus {
  rto: {
    current_seconds: number
    target_seconds: number
    violations: number
    last_failover_duration: number
    failover_count: number
    recovery_count: number
  }
  rpo: {
    current_seconds: number
    target_seconds: number
    violations: number
    current_replication_lag_ms: number
    max_replication_lag_ms: number
    data_loss_bytes: number
  }
  integrity: {
    verified: boolean
    last_check: string
  }
  compliance: {
    rto_compliant: boolean
    rpo_compliant: boolean
  }
}

export interface FailoverEvent {
  id: string
  component: string
  from_instance: string
  to_instance: string
  started_at: string
  completed_at?: string
  duration_seconds?: number
  reason: string
  status: string
}

export interface FailoverHistoryResult {
  events: FailoverEvent[]
  total: number
}

export interface ReplicationRegion {
  name: string
  role: string
  status: string
  replication_lag_ms: number
  health_check_success_rate: number
}

export interface ReplicationSummary {
  regions: Record<string, ReplicationRegion>
  max_replication_lag_ms: number
  estimated_rpo_seconds: number
  unhealthy_regions: string[]
  total_regions: number
  healthy_regions: number
}

export interface HeartbeatTarget {
  component: string
  alive: boolean
  latency_ms: number
  consecutive_success: number
  consecutive_failures: number
  last_check: string
}

export interface HeartbeatStatus {
  targets: HeartbeatTarget[]
  total: number
  alive: number
  dead: number
}

export interface HAFullStatus {
  health: Record<string, any>
  heartbeat: HeartbeatStatus
  failover: Record<string, any>
  degradation: Record<string, any>
  dr: DRStatus
}

export const adminApi = {
  health() {
    return http.get('/admin/health')
  },

  healthDetail() {
    return http.get<HealthDetail>('/admin/health/detail')
  },

  mcpStatus() {
    return http.get<MCPStatus>('/admin/mcp/status')
  },

  failoverStatus() {
    return http.get<FailoverStatus>('/admin/failover/status')
  },

  canaryFlags() {
    return http.get<CanaryFlagsResult>('/admin/canary/flags')
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

  metricsSummary() {
    return http.get<MetricsSummary>('/admin/metrics/summary')
  },

  listUsers(limit?: number, offset?: number) {
    return http.get<UserListResult>('/admin/users', { params: { limit, offset } })
  },

  tokenUsage(userId: string) {
    return http.get<TokenUsage>(`/admin/token/usage/${userId}`)
  },

  tokenBudget(userId: string, sessionId?: string) {
    return http.get<TokenBudget>(`/admin/token/budget/${userId}`, { params: { session_id: sessionId } })
  },

  auditLogs(params?: { event_type?: string; user_id?: string; action?: string; limit?: number; offset?: number }) {
    return http.get<AuditLogsResult>('/admin/audit/logs', { params })
  },

  auditFlush() {
    return http.post('/admin/audit/flush')
  },

  drStatus() {
    return http.get<DRStatus>('/admin/dr/status')
  },

  drMetrics() {
    return http.get<DRStatus>('/admin/dr/metrics')
  },

  drHistory(limit?: number) {
    return http.get<FailoverHistoryResult>('/admin/dr/history', { params: { limit } })
  },

  drVerifyIntegrity() {
    return http.post<{ integrity_verified: boolean; timestamp: string }>('/admin/dr/verify-integrity')
  },

  drReplication() {
    return http.get<ReplicationSummary>('/admin/dr/replication')
  },

  haFullStatus() {
    return http.get<HAFullStatus>('/admin/ha/full-status')
  },

  heartbeatStatus() {
    return http.get<HeartbeatStatus>('/admin/heartbeat/status')
  },
}

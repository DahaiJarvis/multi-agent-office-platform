import http from './request'

export interface BusinessOverview {
  date: string
  total_tasks: number
  success_rate: number
  avg_duration_ms: number
  active_users: number
  total_errors: number
  clarification_count: number
}

export interface IntentItem {
  intent: string
  count: number
}

export interface IntentDistribution {
  date: string
  intents: IntentItem[]
  confidence_levels: Record<string, number>
}

export interface AgentPerfItem {
  agent: string
  total: number
  success: number
  error: number
  success_rate: number
}

export interface AgentPerformance {
  date: string
  agents: AgentPerfItem[]
}

export interface ToolUsageItem {
  tool_name: string
  count: number
}

export interface ToolUsageStats {
  date: string
  tools: ToolUsageItem[]
}

export interface GuardrailStats {
  date: string
  total_blocks: number
  by_check_type: Record<string, number>
  by_action: Record<string, number>
}

export interface SkillUsageItem {
  skill_name: string
  total: number
  by_agent: Record<string, number>
}

export interface SkillUsageStats {
  date: string
  skills: SkillUsageItem[]
}

export interface WorkflowExecItem {
  workflow_id: string
  total: number
  success: number
  error: number
}

export interface WorkflowExecutionStats {
  date: string
  workflows: WorkflowExecItem[]
}

export interface TrendDataPoint {
  date: string
  total_tasks: number
  success_rate: number
  avg_duration_ms: number
  active_users: number
}

export interface BusinessTrend {
  period: string
  data_points: TrendDataPoint[]
}

export const analyticsApi = {
  overview(date?: string) {
    return http.get<BusinessOverview>('/analytics/overview', { params: { date } })
  },

  intentDistribution(date?: string) {
    return http.get<IntentDistribution>('/analytics/intent-distribution', { params: { date } })
  },

  agentPerformance(date?: string) {
    return http.get<AgentPerformance>('/analytics/agent-performance', { params: { date } })
  },

  toolUsage(date?: string) {
    return http.get<ToolUsageStats>('/analytics/tool-usage', { params: { date } })
  },

  guardrailStats(date?: string) {
    return http.get<GuardrailStats>('/analytics/guardrail-stats', { params: { date } })
  },

  skillUsage(date?: string) {
    return http.get<SkillUsageStats>('/analytics/skill-usage', { params: { date } })
  },

  workflowExecution(date?: string) {
    return http.get<WorkflowExecutionStats>('/analytics/workflow-execution', { params: { date } })
  },

  trend(period: string = 'daily', days: number = 7) {
    return http.get<BusinessTrend>('/analytics/trend', { params: { period, days } })
  },
}

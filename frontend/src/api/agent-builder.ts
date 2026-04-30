import http from './request'

export interface CustomAgent {
  agent_id: string
  name: string
  display_name: string
  description: string
  system_prompt: string
  mcp_servers: string[]
  model_tier: string
  temperature: number
  max_rounds: number
  review_required: boolean
  allowed_roles: string[]
  tags: string[]
  icon: string
  status: string
  version: number
  created_by: string
  created_at: number
  updated_at: number
}

export interface AgentVersion {
  version: number
  created_at: number
  created_by: string
  change_summary: string
}

export interface AgentTemplate {
  template_id: string
  name: string
  display_name: string
  description: string
  category: string
  icon: string
  preview_config: Record<string, unknown>
}

export const agentBuilderApi = {
  list(params?: { status?: string; keyword?: string }) {
    return http.get<CustomAgent[]>('/agent-builder/agents', { params })
  },

  create(data: {
    name: string
    display_name?: string
    description?: string
    system_prompt: string
    mcp_servers?: string[]
    model_tier?: string
    temperature?: number
    max_rounds?: number
    review_required?: boolean
    allowed_roles?: string[]
    tags?: string[]
    icon?: string
  }) {
    return http.post<CustomAgent>('/agent-builder/agents', data)
  },

  get(agentId: string) {
    return http.get<CustomAgent>(`/agent-builder/agents/${agentId}`)
  },

  update(agentId: string, data: Partial<CustomAgent>) {
    return http.patch<CustomAgent>(`/agent-builder/agents/${agentId}`, data)
  },

  publish(agentId: string) {
    return http.post<CustomAgent>(`/agent-builder/agents/${agentId}/publish`)
  },

  disable(agentId: string) {
    return http.post<CustomAgent>(`/agent-builder/agents/${agentId}/disable`)
  },

  delete(agentId: string) {
    return http.delete(`/agent-builder/agents/${agentId}`)
  },

  versions(agentId: string) {
    return http.get<AgentVersion[]>(`/agent-builder/agents/${agentId}/versions`)
  },

  rollback(agentId: string, version: number) {
    return http.post<CustomAgent>(`/agent-builder/agents/${agentId}/rollback`, null, { params: { version } })
  },

  templates(category?: string) {
    return http.get<AgentTemplate[]>('/agent-builder/templates', { params: { category } })
  },

  createFromTemplate(templateId: string, data: { name: string; display_name?: string }) {
    const { name, display_name } = data
    const overrides: Record<string, string> = {}
    if (display_name) overrides.display_name = display_name
    return http.post<CustomAgent>(`/agent-builder/templates/${templateId}/instantiate`, {
      template_id: templateId,
      name,
      overrides: Object.keys(overrides).length > 0 ? overrides : undefined
    })
  },
}

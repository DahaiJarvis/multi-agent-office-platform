import http from './request'

export interface WorkflowNode {
  node_id: string
  type: string
  name: string
  description: string
  config: Record<string, unknown>
  position: { x: number; y: number }
  agent_name: string
  tool_name: string
  condition_expr: string
  transform_expr: string
  delay_seconds: number
}

export interface WorkflowEdge {
  edge_id: string
  source_node_id: string
  target_node_id: string
  source_port: string
  target_port: string
  condition: string
  label: string
}

export interface Workflow {
  workflow_id: string
  name: string
  description: string
  version: number
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  status: 'draft' | 'published' | 'disabled'
  created_by: string
  created_at: number
  updated_at: number
  tags: string[]
}

export interface WorkflowExecution {
  execution_id: string
  workflow_id: string
  status: string
  current_node_id: string
  results: Record<string, unknown>
  error: string
  started_at: number
  completed_at: number | null
}

export const workflowApi = {
  list(status?: string) {
    return http.get<Workflow[]>('/workflows', { params: { status } })
  },

  create(data: { name: string; description?: string; tags?: string[] }) {
    return http.post<Workflow>('/workflows', data)
  },

  get(workflowId: string) {
    return http.get<Workflow>(`/workflows/${workflowId}`)
  },

  update(workflowId: string, data: Partial<Pick<Workflow, 'name' | 'description' | 'tags'>>) {
    return http.put<Workflow>(`/workflows/${workflowId}`, data)
  },

  delete(workflowId: string) {
    return http.delete(`/workflows/${workflowId}`)
  },

  publish(workflowId: string) {
    return http.post<Workflow>(`/workflows/${workflowId}/publish`)
  },

  addNode(workflowId: string, node: Partial<WorkflowNode> & { type: string }) {
    return http.post<Workflow>(`/workflows/${workflowId}/nodes`, node)
  },

  updateNode(workflowId: string, nodeId: string, node: Partial<WorkflowNode>) {
    return http.put<Workflow>(`/workflows/${workflowId}/nodes/${nodeId}`, node)
  },

  removeNode(workflowId: string, nodeId: string) {
    return http.delete<Workflow>(`/workflows/${workflowId}/nodes/${nodeId}`)
  },

  addEdge(workflowId: string, edge: Partial<WorkflowEdge> & { source_node_id: string; target_node_id: string }) {
    return http.post<Workflow>(`/workflows/${workflowId}/edges`, edge)
  },

  removeEdge(workflowId: string, edgeId: string) {
    return http.delete<Workflow>(`/workflows/${workflowId}/edges/${edgeId}`)
  },

  execute(workflowId: string, inputData?: Record<string, unknown>) {
    return http.post<WorkflowExecution>(`/workflows/${workflowId}/execute`, { input_data: inputData || {} })
  },

  nodeTypes() {
    return http.get<Record<string, string>>('/workflows/node-types')
  },
}

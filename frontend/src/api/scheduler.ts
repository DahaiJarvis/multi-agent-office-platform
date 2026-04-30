import http from './request'

export interface ScheduledTask {
  task_id: string
  name: string
  trigger_type: string
  trigger_value: string
  agent_name: string
  task_prompt: string
  channel: string
  target_user: string
  tenant_id: string
  enabled: boolean
  last_run_at: number
  next_run_at: number
  created_at: number
}

export interface TaskListResult {
  items: ScheduledTask[]
  total: number
}

export const schedulerApi = {
  create(data: {
    name: string
    trigger_type: string
    trigger_value: string
    agent_name?: string
    task_prompt?: string
    channel?: string
    target_user?: string
    tenant_id?: string
  }) {
    return http.post<ScheduledTask>('/scheduler/tasks', data)
  },

  list(params?: { limit?: number; offset?: number }) {
    return http.get<TaskListResult>('/scheduler/tasks', { params })
  },

  get(taskId: string) {
    return http.get<ScheduledTask>(`/scheduler/tasks/${taskId}`)
  },

  update(taskId: string, data: Partial<Omit<ScheduledTask, 'task_id' | 'last_run_at' | 'next_run_at' | 'created_at'>>) {
    return http.put<ScheduledTask>(`/scheduler/tasks/${taskId}`, data)
  },

  delete(taskId: string) {
    return http.delete(`/scheduler/tasks/${taskId}`)
  },

  toggle(taskId: string) {
    return http.post<ScheduledTask>(`/scheduler/tasks/${taskId}/toggle`)
  },
}

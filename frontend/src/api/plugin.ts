import http from './request'

export interface PluginManifest {
  plugin_id: string
  name: string
  display_name: string
  description: string
  version: string
  author: string
  permissions: string[]
  hooks: string[]
  is_public: boolean
  icon: string
  status: string
  created_at: number
}

export interface PluginInstance {
  plugin_id: string
  status: string
  config: Record<string, unknown>
  enabled_at: number
  error: string
}

export const pluginApi = {
  list() {
    return http.get<PluginManifest[]>('/plugins')
  },

  marketplace(keyword?: string) {
    return http.get<PluginManifest[]>('/plugins/marketplace', { params: { keyword } })
  },

  hooks() {
    return http.get<string[]>('/plugins/hooks')
  },

  get(pluginId: string) {
    return http.get<PluginManifest>(`/plugins/${pluginId}`)
  },

  getInstance(pluginId: string) {
    return http.get<PluginInstance>(`/plugins/${pluginId}/instance`)
  },

  register(data: {
    name: string
    display_name?: string
    description?: string
    version?: string
    author?: string
    permissions?: string[]
    hooks?: string[]
    module_path?: string
    entry_class?: string
    is_public?: boolean
    icon?: string
  }) {
    return http.post<PluginManifest>('/plugins', data)
  },

  enable(pluginId: string, config?: Record<string, unknown>) {
    return http.post<PluginInstance>(`/plugins/${pluginId}/enable`, { config: config || {} })
  },

  disable(pluginId: string) {
    return http.post<PluginInstance>(`/plugins/${pluginId}/disable`)
  },

  unregister(pluginId: string) {
    return http.delete(`/plugins/${pluginId}`)
  },

  install(pluginId: string) {
    return http.post<PluginManifest>(`/plugins/${pluginId}/install`)
  },

  executeHooks(data: {
    hook_point: string
    session_id?: string
    user_id?: string
    agent_name?: string
    data?: Record<string, unknown>
  }) {
    return http.post('/plugins/hooks/execute', data)
  },
}

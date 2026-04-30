import http from './request'

export interface PromptTemplate {
  template_id: string
  name: string
  description: string
  category: string
  template: string
  variables: Array<{ name: string; description: string; default_value: string; required: boolean }>
  tags: string[]
  is_public: boolean
  rating: number
  usage_count: number
  created_by: string
  created_at: number
  updated_at: number
}

export interface PromptExecution {
  template_id: string
  rendered_text: string
  variables_used: Record<string, string>
}

export const promptTemplateApi = {
  list(params?: { category?: string; keyword?: string }) {
    return http.get<PromptTemplate[]>('/prompt-templates', { params })
  },

  recommend(query: string) {
    return http.get<PromptTemplate[]>('/prompt-templates/recommend', { params: { query } })
  },

  categories() {
    return http.get<Record<string, string>>('/prompt-templates/categories')
  },

  get(templateId: string) {
    return http.get<PromptTemplate>(`/prompt-templates/${templateId}`)
  },

  create(data: {
    name: string
    description?: string
    category?: string
    template: string
    variables?: Array<{ name: string; description: string; default_value: string; required: boolean }>
    tags?: string[]
    is_public?: boolean
  }) {
    return http.post<PromptTemplate>('/prompt-templates', data)
  },

  update(templateId: string, data: Partial<PromptTemplate>) {
    return http.put<PromptTemplate>(`/prompt-templates/${templateId}`, data)
  },

  delete(templateId: string) {
    return http.delete(`/prompt-templates/${templateId}`)
  },

  render(templateId: string, variables: Record<string, string>) {
    return http.post<PromptExecution>(`/prompt-templates/${templateId}/render`, { variables })
  },

  rate(templateId: string, rating: number) {
    return http.post<PromptTemplate>(`/prompt-templates/${templateId}/rate`, { rating })
  },
}

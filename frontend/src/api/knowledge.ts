/**
 * 知识库 API 模块
 *
 * 封装与智能文档助手(IDA)交互的所有 REST API 调用。
 * 请求通过平台代理路由 /knowledge 转发到 IDA 服务，
 * 认证由代理层自动处理（RSA-JWT Token 映射）。
 */

import request from './request'

/** 知识库 API 路径前缀，对应后端 knowledge_proxy_routes */
const KNOWLEDGE_PREFIX = '/knowledge'

/** 知识库基础信息接口 */
export interface KnowledgeBase {
  id: string
  name: string
  description: string
  access_level: string
  document_count: number
  created_at: string
  updated_at: string
  is_public?: boolean
  owner_id?: string
  status?: string
  user_permission?: string
  department_id?: string | null
  department_name?: string | null
  total_chunks?: number
  chunk_size?: number
  chunk_overlap?: number
  chunk_strategy?: string
  similarity_threshold?: number
  top_k?: number
}

/** IDA 统一响应包装层 */
export interface IdaResponse<T> {
  code: number
  data: T
  message?: string
}

/** 知识库列表数据接口（IDA data 字段内容） */
export interface KnowledgeBaseListData {
  knowledge_bases: KnowledgeBase[]
  total: number
  page: number
  per_page: number
}

/** 知识库列表响应接口（完整 IDA 响应） */
export type KnowledgeBaseListResponse = IdaResponse<KnowledgeBaseListData>

/** 文档信息接口 */
export interface Document {
  id: string
  name: string
  status: 'pending' | 'processing' | 'completed' | 'failed'
  size: number
  created_at: string
}

/** 文档列表响应接口 */
export type DocumentListResponse = IdaResponse<{
  documents: Document[]
  total: number
  page: number
  per_page: number
}>

/** 文件解析结果项接口 */
export interface ParseResultItem {
  filename: string
  content?: string
  text?: string
  error?: string
}

/** 文件解析响应接口 */
export interface ParseFilesResponse {
  results: ParseResultItem[]
}

/** 问答请求参数接口 */
export interface QARequest {
  query: string
  knowledge_base_id?: string
  session_id?: string
}

/** 问答响应接口 */
export interface QAResponse {
  answer: string
  sources?: Array<{ document_id: string; content: string; score: number }>
}

export const knowledgeApi = {
  /**
   * 获取知识库列表
   * @param page 页码，从 1 开始
   * @param perPage 每页数量，默认 20
   */
  listKnowledgeBases(page = 1, perPage = 20) {
    return request.get<KnowledgeBaseListResponse>(`${KNOWLEDGE_PREFIX}/knowledge-bases`, {
      params: { page, per_page: perPage },
    })
  },

  /**
   * 创建知识库
   * @param data 知识库创建参数
   */
  createKnowledgeBase(data: { name: string; description?: string; access_level?: string }) {
    return request.post<KnowledgeBase>(`${KNOWLEDGE_PREFIX}/knowledge-bases`, data)
  },

  /**
   * 获取知识库详情
   * @param kbId 知识库 ID
   */
  getKnowledgeBase(kbId: string) {
    return request.get<KnowledgeBase>(`${KNOWLEDGE_PREFIX}/knowledge-bases/${kbId}`)
  },

  /**
   * 更新知识库
   * @param kbId 知识库 ID
   * @param data 更新字段
   */
  updateKnowledgeBase(kbId: string, data: Record<string, unknown>) {
    return request.put<KnowledgeBase>(`${KNOWLEDGE_PREFIX}/knowledge-bases/${kbId}`, data)
  },

  /**
   * 删除知识库
   * @param kbId 知识库 ID
   */
  deleteKnowledgeBase(kbId: string) {
    return request.delete(`${KNOWLEDGE_PREFIX}/knowledge-bases/${kbId}`)
  },

  /**
   * 获取文档列表
   * @param kbId 知识库 ID
   * @param page 页码
   * @param perPage 每页数量
   */
  listDocuments(kbId: string, page = 1, perPage = 20) {
    return request.get<DocumentListResponse>(`${KNOWLEDGE_PREFIX}/knowledge-bases/${kbId}/documents`, {
      params: { page, per_page: perPage },
    })
  },

  /**
   * 上传文档到知识库
   * @param kbId 知识库 ID
   * @param file 上传的文件
   * @param folderPath 文件夹路径，默认为根目录
   */
  uploadDocument(kbId: string, file: File, folderPath = '') {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('folder_path', folderPath)
    return request.post(`${KNOWLEDGE_PREFIX}/knowledge-bases/${kbId}/documents`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  /**
   * 删除文档
   * @param kbId 知识库 ID
   * @param docId 文档 ID
   */
  deleteDocument(kbId: string, docId: string) {
    return request.delete(`${KNOWLEDGE_PREFIX}/knowledge-bases/${kbId}/documents/${docId}`)
  },

  /**
   * 智能问答
   * @param data 问答请求参数
   */
  askQuestion(data: QARequest) {
    return request.post<QAResponse>(`${KNOWLEDGE_PREFIX}/qa/ask`, data)
  },

  /**
   * 解析文件内容
   * @param files 待解析的文件列表
   */
  parseFiles(files: File[]) {
    const formData = new FormData()
    files.forEach((f) => formData.append('files', f))
    return request.post<ParseFilesResponse>(`${KNOWLEDGE_PREFIX}/qa/parse-files`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  /**
   * 图片分析
   * @param file 图片文件
   * @param query 分析提示语
   */
  analyzeImage(file: File, query = '请描述这张图片的内容') {
    const formData = new FormData()
    formData.append('image', file)
    formData.append('query', query)
    return request.post<QAResponse>(`${KNOWLEDGE_PREFIX}/qa/analyze-image`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
}

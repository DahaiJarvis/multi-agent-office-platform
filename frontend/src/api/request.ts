import axios from 'axios'
import type { AxiosInstance, InternalAxiosRequestConfig, AxiosResponse } from 'axios'

const API_BASE = import.meta.env.VITE_API_BASE || '/api/v1'

const _http: AxiosInstance = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

_http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = localStorage.getItem('access_token')
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

_http.interceptors.response.use(
  (response: AxiosResponse) => {
    return response.data
  },
  async (error) => {
    const status = error.response?.status
    if (status === 401) {
      const refreshToken = localStorage.getItem('refresh_token')
      if (refreshToken && !error.config._retry) {
        error.config._retry = true
        try {
          const { data } = await axios.post(`${API_BASE}/auth/refresh`, {
            refresh_token: refreshToken,
          })
          localStorage.setItem('access_token', data.access_token)
          localStorage.setItem('refresh_token', data.refresh_token)
          error.config.headers.Authorization = `Bearer ${data.access_token}`
          return _http(error.config)
        } catch {
          localStorage.removeItem('access_token')
          localStorage.removeItem('refresh_token')
          window.location.href = '/login'
        }
      } else {
        localStorage.removeItem('access_token')
        localStorage.removeItem('refresh_token')
        window.location.href = '/login'
      }
    } else if (status === 403) {
      const detail = error.response?.data?.message || error.response?.data?.detail || '权限不足'
      const enhancedError = new Error(detail)
      enhancedError.name = 'PermissionDenied'
      return Promise.reject(enhancedError)
    }
    const message = error.response?.data?.message || error.response?.data?.detail || error.message
    return Promise.reject(new Error(message))
  },
)

const http = {
  get<T = any>(url: string, config?: any): Promise<T> {
    return _http.get<T>(url, config) as Promise<T>
  },
  post<T = any>(url: string, data?: any, config?: any): Promise<T> {
    return _http.post<T>(url, data, config) as Promise<T>
  },
  put<T = any>(url: string, data?: any, config?: any): Promise<T> {
    return _http.put<T>(url, data, config) as Promise<T>
  },
  delete<T = any>(url: string, config?: any): Promise<T> {
    return _http.delete<T>(url, config) as Promise<T>
  },
  patch<T = any>(url: string, data?: any, config?: any): Promise<T> {
    return _http.patch<T>(url, data, config) as Promise<T>
  },
}

export default http

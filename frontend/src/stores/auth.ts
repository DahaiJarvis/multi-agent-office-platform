import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi } from '../api/auth'
import type { LoginParams } from '../api/auth'

export const useAuthStore = defineStore('auth', () => {
  const accessToken = ref(localStorage.getItem('access_token') || '')
  const refreshToken = ref(localStorage.getItem('refresh_token') || '')
  const userId = ref(localStorage.getItem('user_id') || '')
  const roles = ref<string[]>(JSON.parse(localStorage.getItem('user_roles') || '[]'))

  const isLoggedIn = computed(() => !!accessToken.value)
  const isAdmin = computed(() => roles.value.includes('admin'))

  async function login(params: LoginParams) {
    const { data } = await authApi.login(params)
    accessToken.value = data.access_token
    refreshToken.value = data.refresh_token
    userId.value = data.user_id
    roles.value = data.roles

    localStorage.setItem('access_token', data.access_token)
    localStorage.setItem('refresh_token', data.refresh_token)
    localStorage.setItem('user_id', data.user_id)
    localStorage.setItem('user_roles', JSON.stringify(data.roles))
  }

  async function logout() {
    try {
      await authApi.logout(refreshToken.value)
    } catch {
      // ignore
    }
    accessToken.value = ''
    refreshToken.value = ''
    userId.value = ''
    roles.value = []

    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    localStorage.removeItem('user_id')
    localStorage.removeItem('user_roles')
  }

  return { accessToken, refreshToken, userId, roles, isLoggedIn, isAdmin, login, logout }
})

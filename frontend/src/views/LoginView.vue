<template>
  <div class="login-page">
    <div class="login-bg">
      <div class="bg-gradient" />
      <div class="bg-grid" />
    </div>

    <div class="login-container">
      <div class="login-brand">
        <div class="brand-icon">
          <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
            <rect width="32" height="32" rx="8" fill="white" fill-opacity="0.15" />
            <path d="M8 16L14 22L24 10" stroke="white" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </div>
        <h1 class="brand-title">AgentOffice</h1>
        <p class="brand-subtitle">企业级多Agent智能办公平台</p>
      </div>

      <form class="login-form" @submit.prevent="handleLogin">
        <div class="form-header">
          <h2>登录</h2>
          <p>输入您的账号信息以继续</p>
        </div>

        <div class="form-group">
          <label for="userId">用户ID</label>
          <input
            id="userId"
            v-model="form.userId"
            type="text"
            placeholder="请输入用户ID"
            autocomplete="username"
            :disabled="loading"
          />
        </div>

        <div class="form-group">
          <label for="password">密码</label>
          <input
            id="password"
            v-model="form.password"
            type="password"
            placeholder="请输入密码"
            autocomplete="current-password"
            :disabled="loading"
          />
        </div>

        <div v-if="errorMsg" class="form-error">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
            <path d="M8 1a7 7 0 100 14A7 7 0 008 1zm-.75 4a.75.75 0 011.5 0v3a.75.75 0 01-1.5 0V5zM8 11.5a.75.75 0 100-1.5.75.75 0 000 1.5z" />
          </svg>
          <span>{{ errorMsg }}</span>
        </div>

        <button type="submit" class="btn-login" :disabled="loading">
          <span v-if="loading" class="btn-spinner" />
          <span>{{ loading ? '登录中...' : '登录' }}</span>
        </button>

        <div class="demo-accounts">
          <p class="demo-title">测试账号</p>
          <div class="demo-list">
            <button
              v-for="account in demoAccounts"
              :key="account.id"
              type="button"
              class="demo-item"
              @click="fillDemo(account)"
            >
              <span class="demo-role">{{ account.role }}</span>
              <span class="demo-id">{{ account.id }}</span>
            </button>
          </div>
        </div>
      </form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const form = reactive({ userId: '', password: '' })
const loading = ref(false)
const errorMsg = ref('')

const demoAccounts = [
  { id: 'admin001', password: 'admin123', role: '管理员' },
  { id: 'mgr001', password: 'mgr123', role: '经理' },
  { id: 'hr001', password: 'hr123', role: 'HR' },
  { id: 'fin001', password: 'fin123', role: '财务' },
  { id: 'emp001', password: 'emp123', role: '员工' },
]

function fillDemo(account: typeof demoAccounts[number]) {
  form.userId = account.id
  form.password = account.password
  errorMsg.value = ''
}

async function handleLogin() {
  if (!form.userId || !form.password) {
    errorMsg.value = '请输入用户ID和密码'
    return
  }

  loading.value = true
  errorMsg.value = ''

  try {
    await authStore.login({
      user_id: form.userId,
      password: form.password,
    })
    router.push('/')
  } catch (err: any) {
    const detail = err.response?.data?.detail
    const message = err.response?.data?.message
    errorMsg.value = message || detail || '登录失败，请检查用户名和密码'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  overflow: hidden;
}

.login-bg {
  position: absolute;
  inset: 0;
  z-index: 0;
}

.bg-gradient {
  position: absolute;
  inset: 0;
  background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 40%, #312e81 70%, #1e1b4b 100%);
}

.bg-grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px);
  background-size: 60px 60px;
}

.login-container {
  position: relative;
  z-index: 1;
  width: 100%;
  max-width: 420px;
  padding: 20px;
}

.login-brand {
  text-align: center;
  margin-bottom: 36px;
}

.brand-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  border-radius: var(--radius-lg);
  background: rgba(255,255,255,0.1);
  backdrop-filter: blur(12px);
  margin-bottom: 16px;
}

.brand-title {
  font-size: 28px;
  font-weight: 800;
  color: white;
  letter-spacing: -0.5px;
}

.brand-subtitle {
  font-size: 14px;
  color: rgba(255,255,255,0.5);
  margin-top: 6px;
}

.login-form {
  background: var(--color-bg-elevated);
  border-radius: var(--radius-xl);
  padding: 36px 32px;
  box-shadow: var(--shadow-xl);
}

.form-header {
  margin-bottom: 28px;
}

.form-header h2 {
  font-size: 22px;
  font-weight: 700;
  color: var(--color-text);
  margin-bottom: 4px;
}

.form-header p {
  font-size: 14px;
  color: var(--color-text-secondary);
}

.form-group {
  margin-bottom: 20px;
}

.form-group label {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-secondary);
  margin-bottom: 6px;
}

.form-group input {
  width: 100%;
  height: 44px;
  padding: 0 14px;
  border: 1.5px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: 15px;
  color: var(--color-text);
  background: var(--color-bg);
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
  outline: none;
}

.form-group input:focus {
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px var(--color-primary-bg);
}

.form-group input:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.form-error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  background: var(--color-danger-bg);
  color: var(--color-danger);
  border-radius: var(--radius-md);
  font-size: 13px;
  font-weight: 500;
  margin-bottom: 16px;
}

.btn-login {
  width: 100%;
  height: 46px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: var(--color-primary);
  color: white;
  border-radius: var(--radius-md);
  font-size: 15px;
  font-weight: 600;
  transition: background var(--transition-fast), transform var(--transition-fast);
}

.btn-login:hover:not(:disabled) {
  background: var(--color-primary-dark);
  transform: translateY(-1px);
}

.btn-login:active:not(:disabled) {
  transform: translateY(0);
}

.btn-login:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

.btn-spinner {
  width: 18px;
  height: 18px;
  border: 2px solid rgba(255,255,255,0.3);
  border-top-color: white;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

.demo-accounts {
  margin-top: 24px;
  padding-top: 20px;
  border-top: 1px solid var(--color-border-light);
}

.demo-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 10px;
}

.demo-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.demo-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  border-radius: var(--radius-full);
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  font-size: 12px;
  transition: all var(--transition-fast);
}

.demo-item:hover {
  border-color: var(--color-primary-light);
  background: var(--color-primary-bg);
}

.demo-role {
  color: var(--color-primary);
  font-weight: 600;
}

.demo-id {
  color: var(--color-text-secondary);
  font-family: var(--font-mono);
  font-size: 11px;
}
</style>

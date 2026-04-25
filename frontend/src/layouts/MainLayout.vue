<template>
  <div class="main-layout">
    <aside class="sidebar" :class="{ collapsed: sidebarCollapsed }">
      <div class="sidebar-header">
        <div class="sidebar-logo" @click="sidebarCollapsed = !sidebarCollapsed">
          <svg width="24" height="24" viewBox="0 0 32 32" fill="none">
            <rect width="32" height="32" rx="8" fill="rgba(255,255,255,0.1)" />
            <path d="M8 16L14 22L24 10" stroke="#818cf8" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          <transition name="fade">
            <span v-if="!sidebarCollapsed" class="logo-text">AgentOffice</span>
          </transition>
        </div>
      </div>

      <nav class="sidebar-nav">
        <div class="nav-section">
          <span v-if="!sidebarCollapsed" class="nav-section-title">对话</span>
          <router-link to="/" class="nav-item" :class="{ active: $route.name === 'Chat' }">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
              <path d="M2 5a2 2 0 012-2h8a2 2 0 012 2v6a2 2 0 01-2 2H6l-4 3V5z" />
              <path d="M14 7h2a2 2 0 012 2v7l-3-2h-4a2 2 0 01-2-2v-1" opacity="0.4" />
            </svg>
            <transition name="fade">
              <span v-if="!sidebarCollapsed" class="nav-label">智能对话</span>
            </transition>
          </router-link>

          <router-link to="/sessions" class="nav-item" :class="{ active: $route.name === 'Sessions' }">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
              <path fill-rule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4z" />
            </svg>
            <transition name="fade">
              <span v-if="!sidebarCollapsed" class="nav-label">会话管理</span>
            </transition>
          </router-link>
        </div>

        <div v-if="authStore.isAdmin" class="nav-section">
          <span v-if="!sidebarCollapsed" class="nav-section-title">管理</span>
          <router-link to="/admin" class="nav-item" :class="{ active: $route.name === 'Dashboard' }">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
              <path d="M2 10a8 8 0 018-8v8H2z" opacity="0.4" />
              <path d="M12 2a8 8 0 010 16v-8H4a8 8 0 018-8z" />
            </svg>
            <transition name="fade">
              <span v-if="!sidebarCollapsed" class="nav-label">运营仪表盘</span>
            </transition>
          </router-link>

          <router-link to="/admin/health" class="nav-item" :class="{ active: $route.name === 'Health' }">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
              <path fill-rule="evenodd" d="M3 4a1 1 0 011-1h12a1 1 0 011 1v4a5 5 0 01-5 5H8a5 5 0 01-5-5V4zm5 6a1 1 0 012 0v2a1 1 0 11-2 0v-2zm4-3a1 1 0 10-2 0v5a1 1 0 102 0V7z" />
            </svg>
            <transition name="fade">
              <span v-if="!sidebarCollapsed" class="nav-label">健康检查</span>
            </transition>
          </router-link>

          <router-link to="/admin/canary" class="nav-item" :class="{ active: $route.name === 'Canary' }">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
              <path d="M10 2a1 1 0 011 1v1.322A7.007 7.007 0 0116.678 10H18a1 1 0 110 2h-1.322A7.007 7.007 0 0111 16.678V18a1 1 0 11-2 0v-1.322A7.007 7.007 0 014.322 12H3a1 1 0 110-2h1.322A7.007 7.007 0 019 4.322V3a1 1 0 011-1zm0 5a3 3 0 100 6 3 3 0 000-6z" />
            </svg>
            <transition name="fade">
              <span v-if="!sidebarCollapsed" class="nav-label">灰度发布</span>
            </transition>
          </router-link>

          <router-link to="/admin/audit" class="nav-item" :class="{ active: $route.name === 'Audit' }">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
              <path fill-rule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4zm6 1a1 1 0 10-2 0v6.586l-1.293-1.293a1 1 0 00-1.414 1.414l3 3a1 1 0 001.414 0l3-3a1 1 0 00-1.414-1.414L10 11.586V5z" />
            </svg>
            <transition name="fade">
              <span v-if="!sidebarCollapsed" class="nav-label">审计日志</span>
            </transition>
          </router-link>

          <router-link to="/admin/token" class="nav-item" :class="{ active: $route.name === 'TokenBudget' }">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
              <path d="M10 2a8 8 0 100 16 8 8 0 000-16zm1 11H9v-2h2v2zm0-4H9V5h2v4z" />
            </svg>
            <transition name="fade">
              <span v-if="!sidebarCollapsed" class="nav-label">Token预算</span>
            </transition>
          </router-link>
        </div>
      </nav>

      <div class="sidebar-footer">
        <div class="user-info" @click="showUserMenu = !showUserMenu">
          <div class="user-avatar">{{ authStore.userId.charAt(0).toUpperCase() }}</div>
          <transition name="fade">
            <div v-if="!sidebarCollapsed" class="user-detail">
              <span class="user-name">{{ authStore.userId }}</span>
              <span class="user-role">{{ authStore.roles.join(', ') }}</span>
            </div>
          </transition>
        </div>
        <transition name="fade">
          <div v-if="showUserMenu" class="user-menu">
            <button class="menu-item" @click="handleLogout">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
                <path d="M2 3.75A.75.75 0 012.75 3h5.5a.75.75 0 010 1.5H3.5v8h4.75a.75.75 0 010 1.5h-5.5A.75.75 0 012 13.25V3.75zm9.47 2.22a.75.75 0 011.06 0l2.25 2.25a.75.75 0 010 1.06l-2.25 2.25a.75.75 0 11-1.06-1.06l1.22-1.22H6.75a.75.75 0 010-1.5h5.94l-1.22-1.22a.75.75 0 010-1.06z" />
              </svg>
              退出登录
            </button>
          </div>
        </transition>
      </div>
    </aside>

    <div class="main-content">
      <header class="top-header">
        <button class="btn-collapse" @click="sidebarCollapsed = !sidebarCollapsed">
          <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
            <path fill-rule="evenodd" d="M2 4.75A.75.75 0 012.75 4h14.5a.75.75 0 010 1.5H2.75A.75.75 0 012 4.75zm0 5A.75.75 0 012.75 9h14.5a.75.75 0 010 1.5H2.75A.75.75 0 012 9.75zm0 5a.75.75 0 01.75-.75h14.5a.75.75 0 010 1.5H2.75a.75.75 0 01-.75-.75z" />
          </svg>
        </button>
        <div class="header-title">{{ pageTitle }}</div>
        <div class="header-actions">
          <div class="status-dot" :class="healthStatus" />
          <span class="status-text">{{ healthStatus === 'healthy' ? '系统正常' : healthStatus === 'degraded' ? '部分降级' : '检查中...' }}</span>
        </div>
      </header>

      <main class="content-body">
        <router-view v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { adminApi } from '../api/admin'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

const sidebarCollapsed = ref(false)
const showUserMenu = ref(false)
const healthStatus = ref<'healthy' | 'degraded' | 'checking'>('checking')

const pageTitles: Record<string, string> = {
  Chat: '智能对话',
  Sessions: '会话管理',
  Dashboard: '运营仪表盘',
  Health: '健康检查',
  Canary: '灰度发布',
  Audit: '审计日志',
  TokenBudget: 'Token预算',
}

const pageTitle = computed(() => pageTitles[route.name as string] || 'AgentOffice')

async function checkHealth() {
  try {
    await adminApi.health()
    healthStatus.value = 'healthy'
  } catch {
    healthStatus.value = 'degraded'
  }
}

async function handleLogout() {
  await authStore.logout()
  router.push('/login')
}

onMounted(() => {
  checkHealth()
  setInterval(checkHealth, 30000)
})
</script>

<style scoped>
.main-layout {
  display: flex;
  min-height: 100vh;
}

.sidebar {
  width: var(--sidebar-width);
  background: var(--color-bg-sidebar);
  display: flex;
  flex-direction: column;
  transition: width var(--transition-base);
  position: fixed;
  top: 0;
  left: 0;
  bottom: 0;
  z-index: 100;
}

.sidebar.collapsed {
  width: 68px;
}

.sidebar-header {
  padding: 16px;
  border-bottom: 1px solid rgba(255,255,255,0.06);
}

.sidebar-logo {
  display: flex;
  align-items: center;
  gap: 12px;
  cursor: pointer;
  padding: 4px;
}

.logo-text {
  font-size: 18px;
  font-weight: 800;
  color: white;
  letter-spacing: -0.3px;
  white-space: nowrap;
}

.sidebar-nav {
  flex: 1;
  overflow-y: auto;
  padding: 12px 8px;
}

.nav-section {
  margin-bottom: 24px;
}

.nav-section-title {
  display: block;
  font-size: 11px;
  font-weight: 600;
  color: rgba(255,255,255,0.3);
  text-transform: uppercase;
  letter-spacing: 0.8px;
  padding: 0 12px;
  margin-bottom: 6px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  color: var(--color-text-sidebar);
  transition: all var(--transition-fast);
  text-decoration: none;
  white-space: nowrap;
}

.nav-item:hover {
  background: var(--color-bg-sidebar-hover);
  color: var(--color-text-sidebar-active);
}

.nav-item.active {
  background: var(--color-bg-sidebar-active);
  color: var(--color-text-sidebar-active);
}

.nav-label {
  font-size: 14px;
  font-weight: 500;
}

.sidebar-footer {
  padding: 12px;
  border-top: 1px solid rgba(255,255,255,0.06);
  position: relative;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background var(--transition-fast);
}

.user-info:hover {
  background: var(--color-bg-sidebar-hover);
}

.user-avatar {
  width: 34px;
  height: 34px;
  border-radius: var(--radius-full);
  background: var(--color-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-size: 14px;
  font-weight: 700;
  flex-shrink: 0;
}

.user-detail {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.user-name {
  font-size: 13px;
  font-weight: 600;
  color: white;
}

.user-role {
  font-size: 11px;
  color: rgba(255,255,255,0.4);
}

.user-menu {
  position: absolute;
  bottom: 70px;
  left: 12px;
  right: 12px;
  background: var(--color-bg-sidebar-hover);
  border-radius: var(--radius-md);
  overflow: hidden;
  box-shadow: var(--shadow-lg);
}

.menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 10px 14px;
  color: rgba(255,255,255,0.7);
  font-size: 13px;
  transition: all var(--transition-fast);
}

.menu-item:hover {
  background: rgba(255,255,255,0.08);
  color: white;
}

.main-content {
  flex: 1;
  margin-left: var(--sidebar-width);
  transition: margin-left var(--transition-base);
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

.sidebar.collapsed ~ .main-content {
  margin-left: 68px;
}

.top-header {
  height: var(--header-height);
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 0 24px;
  background: var(--color-bg-elevated);
  border-bottom: 1px solid var(--color-border-light);
  position: sticky;
  top: 0;
  z-index: 50;
}

.btn-collapse {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
  transition: all var(--transition-fast);
}

.btn-collapse:hover {
  background: var(--color-bg);
  color: var(--color-text);
}

.header-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--color-text);
}

.header-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.status-dot.healthy {
  background: var(--color-success);
  box-shadow: 0 0 6px rgba(5, 150, 105, 0.4);
}

.status-dot.degraded {
  background: var(--color-warning);
  box-shadow: 0 0 6px rgba(217, 119, 6, 0.4);
}

.status-dot.checking {
  background: var(--color-text-tertiary);
}

.status-text {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.content-body {
  flex: 1;
  padding: 24px;
  overflow-y: auto;
}

@media (max-width: 768px) {
  .sidebar {
    width: 68px;
  }

  .main-content {
    margin-left: 68px;
  }

  .nav-section-title,
  .nav-label,
  .logo-text,
  .user-detail {
    display: none;
  }

  .content-body {
    padding: 16px;
  }
}
</style>

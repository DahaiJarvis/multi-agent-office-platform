import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('../views/LoginView.vue'),
    meta: { requiresAuth: false },
  },
  {
    path: '/',
    component: () => import('../layouts/MainLayout.vue'),
    meta: { requiresAuth: true },
    children: [
      { path: '', name: 'Chat', component: () => import('../views/chat/ChatView.vue') },
      { path: 'sessions', name: 'Sessions', component: () => import('../views/chat/SessionView.vue') },
      { path: 'admin', name: 'Dashboard', component: () => import('../views/admin/DashboardView.vue') },
      { path: 'admin/health', name: 'Health', component: () => import('../views/admin/HealthView.vue') },
      { path: 'admin/canary', name: 'Canary', component: () => import('../views/admin/CanaryView.vue') },
      { path: 'admin/audit', name: 'Audit', component: () => import('../views/admin/AuditView.vue') },
      { path: 'admin/token', name: 'TokenBudget', component: () => import('../views/admin/TokenBudgetView.vue') },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  const token = localStorage.getItem('access_token')
  if (to.meta.requiresAuth !== false && !token) {
    return { name: 'Login' }
  }
  if (to.name === 'Login' && token) {
    return { name: 'Chat' }
  }
})

export default router

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
      { path: 'admin', name: 'Dashboard', component: () => import('../views/admin/DashboardView.vue') },
      { path: 'admin/health', name: 'Health', component: () => import('../views/admin/HealthView.vue') },
      { path: 'admin/canary', name: 'Canary', component: () => import('../views/admin/CanaryView.vue') },
      { path: 'admin/audit', name: 'Audit', component: () => import('../views/admin/AuditView.vue') },
      { path: 'admin/token', name: 'TokenBudget', component: () => import('../views/admin/TokenBudgetView.vue') },
      { path: 'knowledge', name: 'Knowledge', component: () => import('../views/knowledge/KnowledgeView.vue') },
      { path: 'knowledge/:kbId', name: 'KbDocs', component: () => import('../views/knowledge/KbDocsView.vue') },
      { path: 'workflow', name: 'Workflow', component: () => import('../views/workflow/WorkflowView.vue') },
      { path: 'approval', name: 'Approval', component: () => import('../views/approval/ApprovalView.vue') },
      { path: 'scheduler', name: 'Scheduler', component: () => import('../views/scheduler/SchedulerView.vue') },
      { path: 'plugins', name: 'Plugins', component: () => import('../views/plugin/PluginView.vue') },
      { path: 'agent-builder', name: 'AgentBuilder', component: () => import('../views/agent-builder/AgentBuilderView.vue') },
      { path: 'prompt-templates', name: 'PromptTemplates', component: () => import('../views/prompt-template/PromptTemplateView.vue') },
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

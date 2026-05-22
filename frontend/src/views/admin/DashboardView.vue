<template>
  <div class="dashboard-page">
    <div class="page-header">
      <h2>运营仪表盘</h2>
      <div class="header-actions">
        <button class="btn-refresh" @click="loadData">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
            <path fill-rule="evenodd" d="M8 3a5 5 0 110 10A5 5 0 018 3zm0 1.5a3.5 3.5 0 100 7 3.5 3.5 0 000-7z" opacity="0.4" />
            <path d="M8 1a7 7 0 016.95 6.25h-1.5A5.5 5.5 0 008 2.5a5.5 5.5 0 00-5.45 4.75h-1.5A7 7 0 018 1z" />
          </svg>
          刷新
        </button>
      </div>
    </div>

    <div class="guide-banner" v-if="!guideDismissed">
      <div class="guide-banner-content">
        <span class="guide-icon">?</span>
        <span>运营仪表盘展示平台整体运行状态，包括会话统计、Token 消耗、Agent 调用分布和服务健康情况。</span>
        <button class="guide-link" @click="showGuideDialog = true">查看详细指南</button>
        <button class="guide-close" @click="guideDismissed = true">&times;</button>
      </div>
    </div>

    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-icon" style="background: var(--color-primary-bg); color: var(--color-primary)">
          <svg width="22" height="22" viewBox="0 0 20 20" fill="currentColor">
            <path d="M2 5a2 2 0 012-2h8a2 2 0 012 2v6a2 2 0 01-2 2H6l-4 3V5z" />
          </svg>
        </div>
        <div class="stat-detail">
          <span class="stat-value">{{ metrics.total_sessions || 0 }}</span>
          <span class="stat-label">总会话数</span>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-icon" style="background: var(--color-success-bg); color: var(--color-success)">
          <svg width="22" height="22" viewBox="0 0 20 20" fill="currentColor">
            <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" />
          </svg>
        </div>
        <div class="stat-detail">
          <span class="stat-value">{{ metrics.active_sessions || 0 }}</span>
          <span class="stat-label">活跃会话</span>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-icon" style="background: var(--color-warning-bg); color: var(--color-warning)">
          <svg width="22" height="22" viewBox="0 0 20 20" fill="currentColor">
            <path d="M10 2a8 8 0 100 16 8 8 0 000-16zm1 11H9v-2h2v2zm0-4H9V5h2v4z" />
          </svg>
        </div>
        <div class="stat-detail">
          <span class="stat-value">{{ formatTokens(metrics.total_tokens_used || 0) }}</span>
          <span class="stat-label">Token 消耗</span>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-icon" style="background: var(--color-info-bg); color: var(--color-info)">
          <svg width="22" height="22" viewBox="0 0 20 20" fill="currentColor">
            <path d="M8.864.046C7.908-.193 7.02.53 6.956 1.466c-.072 1.051-.23 2.016-.428 2.59-.228.706-.822 1.504-1.724 2.194C4.284 4.457 3.502 4 2.5 4A2.5 2.5 0 000 6.5v3A2.5 2.5 0 002.5 12c.964 0 1.727-.43 2.252-.925a13.36 13.36 0 002.248.825V14.5a1.5 1.5 0 003 0v-2.292c.462-.16.903-.388 1.284-.654l.128-.09c.388-.275.896-.625 1.388-.927h.001c.49-.3.962-.543 1.399-.684.44-.142.768-.158.998-.058A1.5 1.5 0 0016 8.5v-3a1.5 1.5 0 00-1.5-1.5h-2.034c-.272 0-.514-.098-.712-.224-.2-.128-.35-.296-.447-.465A5.922 5.922 0 0110.5 1.5c0-.322-.046-.632-.14-.927a2.435 2.435 0 00-.422-.836 1.743 1.743 0 00-1.074-.69z" />
          </svg>
        </div>
        <div class="stat-detail">
          <span class="stat-value">{{ Math.round((metrics.avg_feedback_score || 0) * 100) }}%</span>
          <span class="stat-label">满意度</span>
        </div>
      </div>
    </div>

    <div class="dashboard-grid">
      <div class="dashboard-card">
        <h3>Agent 调用分布</h3>
        <div class="inline-hint">各 Agent 被调用的次数统计，帮助了解使用热点</div>
        <div v-if="agentData.length === 0" class="empty-hint">暂无数据</div>
        <div v-else class="agent-bars">
          <div v-for="agent in agentData" :key="agent.name" class="agent-bar-row">
            <span class="agent-name">{{ agent.name }}</span>
            <div class="bar-track">
              <div class="bar-fill" :style="{ width: agent.percentage + '%' }" />
            </div>
            <span class="agent-count">{{ agent.count }}</span>
          </div>
        </div>
      </div>

      <div class="dashboard-card">
        <h3>MCP 服务状态</h3>
        <div class="inline-hint">MCP 工具服务的实时健康状态和响应延迟</div>
        <div v-if="mcpLoading" class="empty-hint"><div class="spinner-sm" /></div>
        <div v-else-if="mcpServices.length === 0" class="empty-hint">暂无数据</div>
        <div v-else class="service-list">
          <div v-for="svc in mcpServices" :key="svc.name" class="service-item">
            <div class="service-dot" :class="svc.status" />
            <span class="service-name">{{ svc.name }}</span>
            <span class="service-latency">{{ svc.latency || '-' }}ms</span>
          </div>
        </div>
      </div>

      <div class="dashboard-card">
        <h3>故障转移状态</h3>
        <div class="inline-hint">多可用区部署的故障切换状态，异常时自动切换到备用 AZ</div>
        <div v-if="failoverLoading" class="empty-hint"><div class="spinner-sm" /></div>
        <div v-else class="failover-info">
          <div class="failover-row">
            <span class="fo-label">当前 AZ</span>
            <span class="fo-value">{{ failover.current_az || '-' }}</span>
          </div>
          <div class="failover-row">
            <span class="fo-label">健康状态</span>
            <span class="fo-value" :class="failover.healthy ? 'text-success' : 'text-danger'">
              {{ failover.healthy ? '正常' : '异常' }}
            </span>
          </div>
          <div class="failover-row">
            <span class="fo-label">上次切换</span>
            <span class="fo-value">{{ failover.last_failover || '-' }}</span>
          </div>
        </div>
      </div>

      <div class="dashboard-card">
        <h3>反馈统计</h3>
        <div class="inline-hint">用户对 Agent 回答的点赞/点踩统计，反映回答质量</div>
        <div v-if="feedbackLoading" class="empty-hint"><div class="spinner-sm" /></div>
        <div v-else class="feedback-stats">
          <div class="fb-row">
            <span class="fb-icon positive">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
                <path d="M8.864.046C7.908-.193 7.02.53 6.956 1.466c-.072 1.051-.23 2.016-.428 2.59-.228.706-.822 1.504-1.724 2.194C4.284 4.457 3.502 4 2.5 4A2.5 2.5 0 000 6.5v3A2.5 2.5 0 002.5 12c.964 0 1.727-.43 2.252-.925a13.36 13.36 0 002.248.825V14.5a1.5 1.5 0 003 0v-2.292c.462-.16.903-.388 1.284-.654l.128-.09c.388-.275.896-.625 1.388-.927h.001c.49-.3.962-.543 1.399-.684.44-.142.768-.158.998-.058A1.5 1.5 0 0016 8.5v-3a1.5 1.5 0 00-1.5-1.5h-2.034c-.272 0-.514-.098-.712-.224-.2-.128-.35-.296-.447-.465A5.922 5.922 0 0110.5 1.5c0-.322-.046-.632-.14-.927a2.435 2.435 0 00-.422-.836 1.743 1.743 0 00-1.074-.69z" />
              </svg>
            </span>
            <span class="fb-label">好评</span>
            <span class="fb-count">{{ feedbackStats.thumbs_up || 0 }}</span>
          </div>
          <div class="fb-row">
            <span class="fb-icon negative">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" style="transform: rotate(180deg)">
                <path d="M8.864.046C7.908-.193 7.02.53 6.956 1.466c-.072 1.051-.23 2.016-.428 2.59-.228.706-.822 1.504-1.724 2.194C4.284 4.457 3.502 4 2.5 4A2.5 2.5 0 000 6.5v3A2.5 2.5 0 002.5 12c.964 0 1.727-.43 2.252-.925a13.36 13.36 0 002.248.825V14.5a1.5 1.5 0 003 0v-2.292c.462-.16.903-.388 1.284-.654l.128-.09c.388-.275.896-.625 1.388-.927h.001c.49-.3.962-.543 1.399-.684.44-.142.768-.158.998-.058A1.5 1.5 0 0016 8.5v-3a1.5 1.5 0 00-1.5-1.5h-2.034c-.272 0-.514-.098-.712-.224-.2-.128-.35-.296-.447-.465A5.922 5.922 0 0110.5 1.5c0-.322-.046-.632-.14-.927a2.435 2.435 0 00-.422-.836 1.743 1.743 0 00-1.074-.69z" />
              </svg>
            </span>
            <span class="fb-label">差评</span>
            <span class="fb-count">{{ feedbackStats.thumbs_down || 0 }}</span>
          </div>
        </div>
      </div>
    </div>

    <div class="section-title">
      <h3>业务分析</h3>
    </div>

    <div class="stats-grid analytics-stats">
      <div class="stat-card">
        <div class="stat-icon" style="background: var(--color-primary-bg); color: var(--color-primary)">
          <svg width="22" height="22" viewBox="0 0 20 20" fill="currentColor">
            <path d="M9 2a1 1 0 000 2h2a1 1 0 100-2H9z" />
            <path fill-rule="evenodd" d="M4 5a2 2 0 012-2 3 3 0 003 3h2a3 3 0 003-3 2 2 0 012 2v11a2 2 0 01-2 2H6a2 2 0 01-2-2V5zm3 4a1 1 0 000 2h.01a1 1 0 100-2H7zm3 0a1 1 0 000 2h3a1 1 0 100-2h-3zm-3 4a1 1 0 100 2h.01a1 1 0 100-2H7zm3 0a1 1 0 100 2h3a1 1 0 100-2h-3z" />
          </svg>
        </div>
        <div class="stat-detail">
          <span class="stat-value">{{ bizOverview.total_tasks || 0 }}</span>
          <span class="stat-label">今日任务</span>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-icon" style="background: var(--color-success-bg); color: var(--color-success)">
          <svg width="22" height="22" viewBox="0 0 20 20" fill="currentColor">
            <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" />
          </svg>
        </div>
        <div class="stat-detail">
          <span class="stat-value">{{ bizOverview.success_rate || 0 }}%</span>
          <span class="stat-label">任务成功率</span>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-icon" style="background: var(--color-warning-bg); color: var(--color-warning)">
          <svg width="22" height="22" viewBox="0 0 20 20" fill="currentColor">
            <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm1-12a1 1 0 10-2 0v4a1 1 0 00.293.707l2.828 2.829a1 1 0 101.415-1.415L11 9.586V6z" />
          </svg>
        </div>
        <div class="stat-detail">
          <span class="stat-value">{{ formatDuration(bizOverview.avg_duration_ms || 0) }}</span>
          <span class="stat-label">平均耗时</span>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-icon" style="background: var(--color-danger-bg); color: var(--color-danger)">
          <svg width="22" height="22" viewBox="0 0 20 20" fill="currentColor">
            <path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" />
          </svg>
        </div>
        <div class="stat-detail">
          <span class="stat-value">{{ bizOverview.total_errors || 0 }}</span>
          <span class="stat-label">错误总数</span>
        </div>
      </div>
    </div>

    <div class="dashboard-grid">
      <div class="dashboard-card">
        <h3>意图分布</h3>
        <div class="inline-hint">用户请求的意图分类统计，帮助了解业务热点</div>
        <div v-if="bizLoading" class="empty-hint"><div class="spinner-sm" /></div>
        <div v-else-if="intentDist.length === 0" class="empty-hint">暂无数据</div>
        <div v-else class="agent-bars">
          <div v-for="item in intentDist" :key="item.intent" class="agent-bar-row">
            <span class="agent-name">{{ item.intent }}</span>
            <div class="bar-track">
              <div class="bar-fill intent-bar" :style="{ width: item.percentage + '%' }" />
            </div>
            <span class="agent-count">{{ item.count }}</span>
          </div>
        </div>
      </div>

      <div class="dashboard-card">
        <h3>Agent 性能排行</h3>
        <div class="inline-hint">各 Agent 的调用次数、成功率和错误数</div>
        <div v-if="bizLoading" class="empty-hint"><div class="spinner-sm" /></div>
        <div v-else-if="agentPerf.length === 0" class="empty-hint">暂无数据</div>
        <div v-else class="perf-table">
          <div class="perf-header">
            <span class="perf-col-name">Agent</span>
            <span class="perf-col">调用</span>
            <span class="perf-col">成功率</span>
            <span class="perf-col">错误</span>
          </div>
          <div v-for="item in agentPerf" :key="item.agent" class="perf-row">
            <span class="perf-col-name">{{ item.agent }}</span>
            <span class="perf-col">{{ item.total }}</span>
            <span class="perf-col" :class="item.success_rate >= 90 ? 'text-success' : item.success_rate >= 70 ? 'text-warning' : 'text-danger'">{{ item.success_rate }}%</span>
            <span class="perf-col" :class="item.error > 0 ? 'text-danger' : ''">{{ item.error }}</span>
          </div>
        </div>
      </div>

      <div class="dashboard-card">
        <h3>工具使用排行</h3>
        <div class="inline-hint">各工具的调用频率统计</div>
        <div v-if="bizLoading" class="empty-hint"><div class="spinner-sm" /></div>
        <div v-else-if="toolUsage.length === 0" class="empty-hint">暂无数据</div>
        <div v-else class="agent-bars">
          <div v-for="item in toolUsage" :key="item.tool_name" class="agent-bar-row">
            <span class="agent-name">{{ item.tool_name }}</span>
            <div class="bar-track">
              <div class="bar-fill tool-bar" :style="{ width: item.percentage + '%' }" />
            </div>
            <span class="agent-count">{{ item.count }}</span>
          </div>
        </div>
      </div>

      <div class="dashboard-card">
        <h3>安全拦截统计</h3>
        <div class="inline-hint">安全护栏拦截的请求统计，含检查类型和动作分布</div>
        <div v-if="bizLoading" class="empty-hint"><div class="spinner-sm" /></div>
        <div v-else-if="guardrailStats.total_blocks === 0" class="empty-hint">暂无拦截记录</div>
        <div v-else class="guardrail-info">
          <div class="guardrail-summary">
            <span class="guardrail-label">拦截总数</span>
            <span class="guardrail-value text-danger">{{ guardrailStats.total_blocks }}</span>
          </div>
          <div class="guardrail-detail" v-if="Object.keys(guardrailStats.by_check_type || {}).length > 0">
            <span class="guardrail-sublabel">按检查类型</span>
            <div class="guardrail-tags">
              <span v-for="(count, type) in guardrailStats.by_check_type" :key="type" class="guardrail-tag">
                {{ type }}: {{ count }}
              </span>
            </div>
          </div>
          <div class="guardrail-detail" v-if="Object.keys(guardrailStats.by_action || {}).length > 0">
            <span class="guardrail-sublabel">按动作类型</span>
            <div class="guardrail-tags">
              <span v-for="(count, action) in guardrailStats.by_action" :key="action" class="guardrail-tag">
                {{ action }}: {{ count }}
              </span>
            </div>
          </div>
        </div>
      </div>

      <div class="dashboard-card">
        <h3>技能使用统计</h3>
        <div class="inline-hint">各技能的调用次数和按 Agent 的使用分布</div>
        <div v-if="bizLoading" class="empty-hint"><div class="spinner-sm" /></div>
        <div v-else-if="skillUsage.length === 0" class="empty-hint">暂无数据</div>
        <div v-else class="agent-bars">
          <div v-for="item in skillUsage" :key="item.skill_name" class="agent-bar-row">
            <span class="agent-name">{{ item.skill_name }}</span>
            <div class="bar-track">
              <div class="bar-fill skill-bar" :style="{ width: item.percentage + '%' }" />
            </div>
            <span class="agent-count">{{ item.total }}</span>
          </div>
        </div>
      </div>

      <div class="dashboard-card">
        <h3>工作流执行统计</h3>
        <div class="inline-hint">各工作流的执行次数、成功率和错误数</div>
        <div v-if="bizLoading" class="empty-hint"><div class="spinner-sm" /></div>
        <div v-else-if="workflowExec.length === 0" class="empty-hint">暂无数据</div>
        <div v-else class="perf-table">
          <div class="perf-header">
            <span class="perf-col-name">工作流</span>
            <span class="perf-col">执行</span>
            <span class="perf-col">成功</span>
            <span class="perf-col">错误</span>
          </div>
          <div v-for="item in workflowExec" :key="item.workflow_id" class="perf-row">
            <span class="perf-col-name">{{ item.workflow_id }}</span>
            <span class="perf-col">{{ item.total }}</span>
            <span class="perf-col text-success">{{ item.success }}</span>
            <span class="perf-col" :class="item.error > 0 ? 'text-danger' : ''">{{ item.error }}</span>
          </div>
        </div>
      </div>
    </div>

    <el-dialog v-model="showGuideDialog" title="运营仪表盘使用指南" width="640px">
      <div class="guide-content">
        <div class="guide-section">
          <h4>仪表盘概览</h4>
          <p>运营仪表盘是平台运行状态的全局视图，帮助管理员实时监控会话量、Token 消耗、服务健康等关键指标，及时发现和定位问题。</p>
        </div>
        <div class="guide-section">
          <h4>指标说明</h4>
          <div class="config-list">
            <div class="config-item"><code>总会话数</code> - 平台累计创建的对话会话总数</div>
            <div class="config-item"><code>活跃会话</code> - 当前正在进行中的会话数量</div>
            <div class="config-item"><code>Token 消耗</code> - 累计消耗的 Token 数量（K=千, M=百万）</div>
            <div class="config-item"><code>满意度</code> - 基于用户反馈计算的满意度百分比</div>
          </div>
        </div>
        <div class="guide-section">
          <h4>功能卡片</h4>
          <div class="config-list">
            <div class="config-item"><code>Agent 调用分布</code> - 展示各 Agent 被调用的频率，识别高频和低频 Agent</div>
            <div class="config-item"><code>MCP 服务状态</code> - 监控工具服务的健康状态和响应延迟，绿点=正常，红点=异常</div>
            <div class="config-item"><code>故障转移状态</code> - 多可用区部署的切换状态，异常时自动切换到备用可用区</div>
            <div class="config-item"><code>反馈统计</code> - 用户的点赞/点踩汇总，评估 Agent 回答质量</div>
          </div>
        </div>
        <div class="guide-section">
          <h4>使用建议</h4>
          <p>定期查看仪表盘，关注 Token 消耗趋势和满意度变化。当 MCP 服务出现红点或故障转移状态异常时，需及时排查原因。</p>
        </div>
      </div>
      <template #footer>
        <el-button type="primary" @click="showGuideDialog = false">知道了</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { adminApi } from '../../api/admin'
import { agentApi } from '../../api/agent'
import { analyticsApi } from '../../api/analytics'
import type { BusinessOverview, IntentDistribution, AgentPerformance, ToolUsageStats, GuardrailStats, SkillUsageStats, WorkflowExecutionStats } from '../../api/analytics'

const metrics = reactive<any>({})
const agentData = ref<any[]>([])
const mcpServices = ref<any[]>([])
const mcpLoading = ref(false)
const failover = reactive<any>({})
const failoverLoading = ref(false)
const feedbackStats = reactive<any>({})
const feedbackLoading = ref(false)
const guideDismissed = ref(false)
const showGuideDialog = ref(false)

const bizLoading = ref(false)
const bizOverview = reactive<BusinessOverview>({ date: '', total_tasks: 0, success_rate: 0, avg_duration_ms: 0, active_users: 0, total_errors: 0, clarification_count: 0 })
const intentDist = ref<any[]>([])
const agentPerf = ref<any[]>([])
const toolUsage = ref<any[]>([])
const guardrailStats = reactive<GuardrailStats>({ date: '', total_blocks: 0, by_check_type: {}, by_action: {} })
const skillUsage = ref<any[]>([])
const workflowExec = ref<any[]>([])

function formatTokens(n: number): string {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K'
  return String(n)
}

function formatDuration(ms: number): string {
  if (ms >= 60000) return (ms / 60000).toFixed(1) + 'min'
  if (ms >= 1000) return (ms / 1000).toFixed(1) + 's'
  return ms.toFixed(0) + 'ms'
}

async function loadData() {
  try {
    const data = await adminApi.metricsSummary()
    Object.assign(metrics, data)
    if (data.agent_distribution) {
      const maxCount = Math.max(...data.agent_distribution.map((a: any) => a.count), 1)
      agentData.value = data.agent_distribution.map((a: any) => ({
        ...a,
        percentage: (a.count / maxCount) * 100,
      }))
    }
  } catch {
    ElMessage.error('加载 Agent 统计失败')
  }

  mcpLoading.value = true
  try {
    const data = await adminApi.mcpStatus()
    mcpServices.value = data.services || data || []
  } catch {
    ElMessage.error('加载 MCP 服务状态失败')
  }
  finally { mcpLoading.value = false }

  failoverLoading.value = true
  try {
    const data = await adminApi.failoverStatus()
    Object.assign(failover, data)
  } catch {
    ElMessage.error('加载故障转移状态失败')
  }
  finally { failoverLoading.value = false }

  feedbackLoading.value = true
  try {
    const data = await agentApi.getFeedbackStats()
    Object.assign(feedbackStats, data)
  } catch {
    ElMessage.error('加载反馈统计失败')
  }
  finally { feedbackLoading.value = false }

  bizLoading.value = true
  try {
    const [overviewData, intentData, agentPerfData, toolData, guardrailData, skillData, workflowData] = await Promise.allSettled([
      analyticsApi.overview(),
      analyticsApi.intentDistribution(),
      analyticsApi.agentPerformance(),
      analyticsApi.toolUsage(),
      analyticsApi.guardrailStats(),
      analyticsApi.skillUsage(),
      analyticsApi.workflowExecution(),
    ])

    if (overviewData.status === 'fulfilled') {
      Object.assign(bizOverview, overviewData.value)
    }

    if (intentData.status === 'fulfilled' && intentData.value.intents) {
      const maxCount = Math.max(...intentData.value.intents.map((a: any) => a.count), 1)
      intentDist.value = intentData.value.intents.map((a: any) => ({
        ...a,
        percentage: (a.count / maxCount) * 100,
      }))
    }

    if (agentPerfData.status === 'fulfilled' && agentPerfData.value.agents) {
      agentPerf.value = agentPerfData.value.agents
    }

    if (toolData.status === 'fulfilled' && toolData.value.tools) {
      const maxCount = Math.max(...toolData.value.tools.map((a: any) => a.count), 1)
      toolUsage.value = toolData.value.tools.map((a: any) => ({
        ...a,
        percentage: (a.count / maxCount) * 100,
      }))
    }

    if (guardrailData.status === 'fulfilled') {
      Object.assign(guardrailStats, guardrailData.value)
    }

    if (skillData.status === 'fulfilled' && skillData.value.skills) {
      const maxCount = Math.max(...skillData.value.skills.map((a: any) => a.total), 1)
      skillUsage.value = skillData.value.skills.map((a: any) => ({
        ...a,
        percentage: (a.total / maxCount) * 100,
      }))
    }

    if (workflowData.status === 'fulfilled' && workflowData.value.workflows) {
      workflowExec.value = workflowData.value.workflows
    }
  } catch {
    ElMessage.error('加载业务分析数据失败')
  }
  finally { bizLoading.value = false }
}

onMounted(loadData)
</script>

<style scoped>
.dashboard-page {
  max-width: 1100px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
}

.page-header h2 {
  font-size: 20px;
  font-weight: 700;
}

.btn-refresh {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  color: var(--color-text-secondary);
  font-size: 13px;
  font-weight: 500;
  transition: all var(--transition-fast);
}

.btn-refresh:hover {
  border-color: var(--color-primary-light);
  color: var(--color-primary);
  background: var(--color-primary-bg);
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}

.stat-card {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 20px;
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  transition: all var(--transition-fast);
}

.stat-card:hover {
  box-shadow: var(--shadow-md);
  transform: translateY(-2px);
}

.stat-icon {
  width: 48px;
  height: 48px;
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.stat-detail {
  display: flex;
  flex-direction: column;
}

.stat-value {
  font-size: 24px;
  font-weight: 800;
  color: var(--color-text);
  line-height: 1.2;
}

.stat-label {
  font-size: 13px;
  color: var(--color-text-secondary);
  margin-top: 2px;
}

.dashboard-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
}

.dashboard-card {
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  padding: 20px;
}

.dashboard-card h3 {
  font-size: 14px;
  font-weight: 700;
  color: var(--color-text);
  margin-bottom: 16px;
}

.empty-hint {
  text-align: center;
  padding: 20px;
  color: var(--color-text-tertiary);
  font-size: 13px;
}

.spinner-sm {
  width: 16px;
  height: 16px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
  margin: 0 auto;
}

.agent-bars {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.agent-bar-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.agent-name {
  font-size: 13px;
  font-weight: 500;
  min-width: 100px;
  color: var(--color-text);
}

.bar-track {
  flex: 1;
  height: 8px;
  background: var(--color-bg);
  border-radius: var(--radius-full);
  overflow: hidden;
}

.bar-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--color-primary), var(--color-primary-light));
  border-radius: var(--radius-full);
  transition: width 0.6s ease;
}

.agent-count {
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--color-text-secondary);
  min-width: 40px;
  text-align: right;
}

.service-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.service-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 0;
}

.service-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.service-dot.healthy, .service-dot.active, .service-dot.online {
  background: var(--color-success);
  box-shadow: 0 0 6px rgba(5, 150, 105, 0.3);
}

.service-dot.unhealthy, .service-dot.offline {
  background: var(--color-danger);
}

.service-dot.degraded {
  background: var(--color-warning);
}

.service-name {
  flex: 1;
  font-size: 13px;
  font-weight: 500;
}

.service-latency {
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--color-text-tertiary);
}

.failover-info {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.failover-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.fo-label {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.fo-value {
  font-size: 13px;
  font-weight: 600;
}

.text-success { color: var(--color-success); }
.text-danger { color: var(--color-danger); }

.feedback-stats {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.fb-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.fb-icon {
  width: 32px;
  height: 32px;
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
}

.fb-icon.positive {
  background: var(--color-success-bg);
  color: var(--color-success);
}

.fb-icon.negative {
  background: var(--color-danger-bg);
  color: var(--color-danger);
}

.fb-label {
  flex: 1;
  font-size: 13px;
  color: var(--color-text-secondary);
}

.fb-count {
  font-size: 18px;
  font-weight: 700;
  color: var(--color-text);
}

@media (max-width: 768px) {
  .stats-grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .dashboard-grid {
    grid-template-columns: 1fr;
  }
}
.header-actions { display: flex; gap: 10px; }
.btn-outline { padding: 7px 14px; border-radius: var(--radius-md); border: 1px solid var(--color-primary); color: var(--color-primary); font-weight: 600; font-size: 13px; transition: all var(--transition-fast); }
.btn-outline:hover { background: rgba(99,102,241,0.06); }
.guide-banner { background: rgba(99,102,241,0.06); border: 1px solid rgba(99,102,241,0.15); border-radius: var(--radius-lg); padding: 12px 16px; margin-bottom: 16px; }
.guide-banner-content { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--color-text-secondary); line-height: 1.5; }
.guide-icon { width: 22px; height: 22px; border-radius: 50%; background: var(--color-primary); color: white; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; flex-shrink: 0; }
.guide-link { color: var(--color-primary); font-weight: 500; cursor: pointer; white-space: nowrap; }
.guide-link:hover { text-decoration: underline; }
.guide-close { margin-left: auto; color: var(--color-text-tertiary); font-size: 18px; cursor: pointer; padding: 0 4px; line-height: 1; }
.guide-close:hover { color: var(--color-text); }
.inline-hint { font-size: 12px; color: var(--color-text-tertiary); margin-bottom: 12px; line-height: 1.5; }
.guide-content { max-height: 60vh; overflow-y: auto; }
.guide-section { margin-bottom: 20px; }
.guide-section h4 { font-size: 15px; font-weight: 600; color: var(--color-text); margin: 0 0 8px; }
.guide-section p { font-size: 13px; color: var(--color-text-secondary); line-height: 1.6; margin: 0 0 8px; }
.guide-section code { background: rgba(99,102,241,0.08); color: var(--color-primary); padding: 1px 6px; border-radius: 4px; font-size: 12px; }
.config-list { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
.config-item { font-size: 13px; color: var(--color-text-secondary); line-height: 1.6; }
.config-item code { background: rgba(99,102,241,0.08); color: var(--color-primary); padding: 1px 6px; border-radius: 4px; font-size: 12px; }

.section-title { margin: 28px 0 16px; border-bottom: 1px solid var(--color-border-light); padding-bottom: 8px; }
.section-title h3 { font-size: 16px; font-weight: 700; color: var(--color-text); }
.analytics-stats { margin-bottom: 16px; }

.perf-table { display: flex; flex-direction: column; gap: 4px; }
.perf-header { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--color-border-light); }
.perf-row { display: flex; align-items: center; gap: 8px; padding: 6px 0; }
.perf-col-name { flex: 2; font-size: 13px; font-weight: 500; color: var(--color-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.perf-col { flex: 1; font-size: 13px; font-family: var(--font-mono); color: var(--color-text-secondary); text-align: right; }
.perf-header .perf-col { font-weight: 600; color: var(--color-text-tertiary); font-size: 12px; }
.perf-header .perf-col-name { font-weight: 600; color: var(--color-text-tertiary); font-size: 12px; }
.text-warning { color: var(--color-warning); }

.intent-bar { background: linear-gradient(90deg, #6366f1, #818cf8); }
.tool-bar { background: linear-gradient(90deg, #059669, #34d399); }
.skill-bar { background: linear-gradient(90deg, #d97706, #fbbf24); }

.guardrail-info { display: flex; flex-direction: column; gap: 12px; }
.guardrail-summary { display: flex; justify-content: space-between; align-items: center; }
.guardrail-label { font-size: 13px; color: var(--color-text-secondary); }
.guardrail-value { font-size: 18px; font-weight: 700; }
.guardrail-detail { display: flex; flex-direction: column; gap: 6px; }
.guardrail-sublabel { font-size: 12px; color: var(--color-text-tertiary); font-weight: 600; }
.guardrail-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.guardrail-tag { font-size: 12px; padding: 2px 8px; border-radius: var(--radius-md); background: var(--color-bg); border: 1px solid var(--color-border-light); color: var(--color-text-secondary); }
</style>

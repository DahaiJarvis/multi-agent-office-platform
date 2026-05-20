<template>
  <div class="routing-page">
    <div class="page-header">
      <h2>意图路由配置</h2>
      <div class="header-actions">
        <button class="btn-refresh" @click="loadAll">
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
        <span>意图路由配置管理所有意图标签、Agent 能力卡片和路由规则，配置来源于 YAML 文件外置化，修改配置文件后重启服务即可生效。</span>
        <button class="guide-close" @click="guideDismissed = true">&times;</button>
      </div>
    </div>

    <div class="tab-bar">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="tab-btn"
        :class="{ active: activeTab === tab.key }"
        @click="activeTab = tab.key"
      >
        {{ tab.label }}
        <span class="tab-count" v-if="tab.count !== undefined">{{ tab.count }}</span>
      </button>
    </div>

    <div v-if="loading" class="loading-state">
      <div class="spinner" />
      <span>加载中...</span>
    </div>

    <div v-else>
      <!-- 意图标签列表 -->
      <div v-if="activeTab === 'intents'" class="tab-content">
        <div v-if="intents.length === 0" class="empty-state">
          <p>暂无意图标签配置</p>
        </div>
        <div v-else class="intent-grid">
          <div v-for="intent in intents" :key="intent.name" class="intent-card">
            <div class="intent-header">
              <span class="intent-name">{{ intent.name }}</span>
              <span class="intent-label">{{ intent.label }}</span>
            </div>
            <p class="intent-desc">{{ intent.description }}</p>
          </div>
        </div>

        <div v-if="examples.length > 0" class="examples-section">
          <h3 class="section-title">分类示例</h3>
          <div class="examples-table">
            <div class="table-header">
              <span class="col-input">用户输入</span>
              <span class="col-output">意图标签</span>
              <span class="col-reason">说明</span>
            </div>
            <div v-for="(ex, idx) in examples" :key="idx" class="table-row">
              <span class="col-input">{{ ex.input }}</span>
              <span class="col-output">
                <span class="intent-tag">{{ ex.output }}</span>
              </span>
              <span class="col-reason">{{ ex.reason || '-' }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 能力卡片列表 -->
      <div v-if="activeTab === 'capabilities'" class="tab-content">
        <div v-if="capabilities.length === 0" class="empty-state">
          <p>暂无能力卡片配置</p>
        </div>
        <div v-else class="capability-list">
          <div v-for="card in capabilities" :key="card.agent_name" class="capability-card">
            <div class="cap-header">
              <div class="cap-title-row">
                <span class="cap-name">{{ card.agent_name }}</span>
                <span class="cap-category">{{ card.category }}</span>
                <span class="cap-version">v{{ card.version }}</span>
                <span class="cap-status" :class="{ enabled: card.enabled, disabled: !card.enabled }">
                  {{ card.enabled ? '已启用' : '已禁用' }}
                </span>
              </div>
              <p class="cap-desc">{{ card.description }}</p>
            </div>

            <div class="cap-body">
              <div class="cap-section">
                <span class="cap-section-title">支持的意图</span>
                <div class="cap-tags">
                  <span v-for="intent in card.supported_intents" :key="intent" class="intent-tag">
                    {{ intent }}
                  </span>
                </div>
              </div>

              <div class="cap-section" v-if="card.intent_configs.length > 0">
                <span class="cap-section-title">意图路由配置</span>
                <div class="intent-config-table">
                  <div class="table-header">
                    <span class="col-intent">意图</span>
                    <span class="col-mode">协作模式</span>
                    <span class="col-review">需要审核</span>
                  </div>
                  <div v-for="cfg in card.intent_configs" :key="cfg.intent" class="table-row">
                    <span class="col-intent">{{ cfg.intent }}</span>
                    <span class="col-mode">
                      <span class="mode-badge" :class="cfg.mode">{{ modeLabels[cfg.mode] || cfg.mode }}</span>
                    </span>
                    <span class="col-review">
                      <span class="review-indicator" :class="{ required: cfg.review }">
                        {{ cfg.review ? '是' : '否' }}
                      </span>
                    </span>
                  </div>
                </div>
              </div>

              <div class="cap-section" v-if="card.required_services.length > 0">
                <span class="cap-section-title">依赖服务</span>
                <div class="cap-tags">
                  <span v-for="svc in card.required_services" :key="svc" class="service-tag">
                    {{ svc }}
                  </span>
                </div>
              </div>

              <div class="cap-section" v-if="card.security_constraints.length > 0">
                <span class="cap-section-title">安全约束</span>
                <ul class="constraint-list">
                  <li v-for="(c, idx) in card.security_constraints" :key="idx">{{ c }}</li>
                </ul>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 路由表 -->
      <div v-if="activeTab === 'routing'" class="tab-content">
        <div v-if="routes.length === 0" class="empty-state">
          <p>暂无路由配置</p>
        </div>
        <div v-else class="routing-table">
          <div class="table-header">
            <span class="col-intent">意图标签</span>
            <span class="col-agent">目标 Agent</span>
            <span class="col-mode">协作模式</span>
            <span class="col-review">需要审核</span>
          </div>
          <div v-for="route in routes" :key="route.intent" class="table-row">
            <span class="col-intent">
              <span class="intent-tag">{{ route.intent }}</span>
            </span>
            <span class="col-agent">{{ route.agent }}</span>
            <span class="col-mode">
              <span class="mode-badge" :class="route.mode">{{ modeLabels[route.mode] || route.mode }}</span>
            </span>
            <span class="col-review">
              <span class="review-indicator" :class="{ required: route.review }">
                {{ route.review ? '是' : '否' }}
              </span>
            </span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { routingApi } from '../../api/routing'
import type { IntentDefinition, IntentExample, CapabilityCard, RoutingEntry } from '../../api/routing'

const activeTab = ref('intents')
const loading = ref(false)
const guideDismissed = ref(false)

const intents = ref<IntentDefinition[]>([])
const examples = ref<IntentExample[]>([])
const capabilities = ref<CapabilityCard[]>([])
const routes = ref<RoutingEntry[]>([])

const modeLabels: Record<string, string> = {
  direct: '直接执行',
  selector: '审核模式',
  swarm: '协作模式',
}

const tabs = computed(() => [
  { key: 'intents', label: '意图标签', count: intents.value.length },
  { key: 'capabilities', label: '能力卡片', count: capabilities.value.length },
  { key: 'routing', label: '路由表', count: routes.value.length },
])

async function loadAll() {
  loading.value = true
  try {
    const [intentsResult, capabilitiesResult, routingResult] = await Promise.all([
      routingApi.getIntents(),
      routingApi.getCapabilities(),
      routingApi.getRouting(),
    ])
    intents.value = intentsResult.intents
    examples.value = intentsResult.examples
    capabilities.value = capabilitiesResult
    routes.value = routingResult.routes
  } catch (err) {
    console.error('加载意图路由配置失败:', err)
  } finally {
    loading.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.routing-page {
  padding: 24px;
  max-width: 1200px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.page-header h2 {
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.btn-refresh {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  background: var(--bg-primary);
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 13px;
  transition: all 0.2s;
}

.btn-refresh:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.guide-banner {
  background: var(--bg-info);
  border: 1px solid var(--border-info);
  border-radius: 8px;
  padding: 12px 16px;
  margin-bottom: 20px;
}

.guide-banner-content {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  color: var(--text-secondary);
}

.guide-icon {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--color-primary);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
}

.guide-close {
  margin-left: auto;
  background: none;
  border: none;
  color: var(--text-tertiary);
  cursor: pointer;
  font-size: 18px;
  padding: 0 4px;
}

.tab-bar {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--border-color);
  margin-bottom: 20px;
}

.tab-btn {
  padding: 10px 20px;
  border: none;
  background: none;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 14px;
  border-bottom: 2px solid transparent;
  transition: all 0.2s;
  display: flex;
  align-items: center;
  gap: 6px;
}

.tab-btn:hover {
  color: var(--text-primary);
}

.tab-btn.active {
  color: var(--color-primary);
  border-bottom-color: var(--color-primary);
}

.tab-count {
  font-size: 11px;
  background: var(--bg-tertiary);
  color: var(--text-tertiary);
  padding: 1px 6px;
  border-radius: 10px;
}

.tab-btn.active .tab-count {
  background: rgba(99, 102, 241, 0.1);
  color: var(--color-primary);
}

.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 60px 0;
  color: var(--text-tertiary);
}

.spinner {
  width: 32px;
  height: 32px;
  border: 3px solid var(--border-color);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.empty-state {
  text-align: center;
  padding: 60px 0;
  color: var(--text-tertiary);
}

.intent-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 12px;
}

.intent-card {
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 16px;
  transition: box-shadow 0.2s;
}

.intent-card:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.intent-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.intent-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-primary);
  font-family: 'SF Mono', 'Fira Code', monospace;
}

.intent-label {
  font-size: 12px;
  color: var(--text-tertiary);
  background: var(--bg-tertiary);
  padding: 2px 8px;
  border-radius: 4px;
}

.intent-desc {
  font-size: 13px;
  color: var(--text-secondary);
  margin: 0;
  line-height: 1.5;
}

.intent-tag {
  display: inline-block;
  font-size: 12px;
  font-family: 'SF Mono', 'Fira Code', monospace;
  background: rgba(99, 102, 241, 0.08);
  color: var(--color-primary);
  padding: 2px 8px;
  border-radius: 4px;
}

.examples-section {
  margin-top: 24px;
}

.section-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 12px 0;
}

.examples-table,
.intent-config-table,
.routing-table {
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  overflow: hidden;
}

.table-header {
  display: flex;
  background: var(--bg-tertiary);
  padding: 10px 16px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.table-row {
  display: flex;
  padding: 10px 16px;
  border-top: 1px solid var(--border-color);
  font-size: 13px;
  color: var(--text-secondary);
  align-items: center;
}

.table-row:hover {
  background: var(--bg-hover);
}

.col-input { flex: 2; }
.col-output { flex: 1; }
.col-reason { flex: 1; }
.col-intent { flex: 1.5; }
.col-agent { flex: 1; }
.col-mode { flex: 1; }
.col-review { flex: 0.8; }

.capability-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.capability-card {
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  overflow: hidden;
}

.cap-header {
  padding: 16px 20px;
  border-bottom: 1px solid var(--border-color);
}

.cap-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.cap-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.cap-category {
  font-size: 11px;
  color: var(--text-tertiary);
  background: var(--bg-tertiary);
  padding: 2px 8px;
  border-radius: 4px;
  text-transform: uppercase;
}

.cap-version {
  font-size: 11px;
  color: var(--text-tertiary);
  font-family: 'SF Mono', 'Fira Code', monospace;
}

.cap-status {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 4px;
  margin-left: auto;
}

.cap-status.enabled {
  background: rgba(34, 197, 94, 0.1);
  color: #16a34a;
}

.cap-status.disabled {
  background: rgba(239, 68, 68, 0.1);
  color: #dc2626;
}

.cap-desc {
  font-size: 13px;
  color: var(--text-secondary);
  margin: 0;
}

.cap-body {
  padding: 16px 20px;
}

.cap-section {
  margin-bottom: 16px;
}

.cap-section:last-child {
  margin-bottom: 0;
}

.cap-section-title {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 8px;
}

.cap-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.service-tag {
  display: inline-block;
  font-size: 12px;
  background: rgba(245, 158, 11, 0.08);
  color: #d97706;
  padding: 2px 8px;
  border-radius: 4px;
}

.constraint-list {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.8;
}

.mode-badge {
  display: inline-block;
  font-size: 12px;
  padding: 2px 10px;
  border-radius: 4px;
  font-weight: 500;
}

.mode-badge.direct {
  background: rgba(34, 197, 94, 0.1);
  color: #16a34a;
}

.mode-badge.selector {
  background: rgba(245, 158, 11, 0.1);
  color: #d97706;
}

.mode-badge.swarm {
  background: rgba(99, 102, 241, 0.1);
  color: #6366f1;
}

.review-indicator {
  font-size: 13px;
  color: var(--text-tertiary);
}

.review-indicator.required {
  color: #d97706;
  font-weight: 500;
}
</style>

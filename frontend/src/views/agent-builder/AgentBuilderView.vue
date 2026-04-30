<template>
  <div class="agent-builder-page">
    <div class="page-header">
      <h2>Agent 构建器</h2>
      <div class="header-actions">
        <button class="btn-refresh" @click="loadAgents">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path fill-rule="evenodd" d="M8 3a5 5 0 110 10A5 5 0 018 3zm0 1.5a3.5 3.5 0 100 7 3.5 3.5 0 000-7z" opacity="0.4" /><path d="M8 1a7 7 0 016.95 6.25h-1.5A5.5 5.5 0 008 2.5a5.5 5.5 0 00-5.45 4.75h-1.5A7 7 0 018 1z" /></svg>
          刷新
        </button>
        <button class="btn-secondary" @click="loadTemplates">从模板创建</button>
        <button class="btn-primary" @click="openCreateDialog">创建 Agent</button>
      </div>
    </div>

    <div class="guide-banner" v-if="!guideDismissed">
      <div class="guide-banner-content">
        <span class="guide-icon">?</span>
        <span>Agent 是具有特定角色和能力的 AI 助手。通过配置系统提示词、模型参数和工具，可以创建适用于不同场景的专属 Agent。</span>
        <button class="guide-link" @click="showGuideDialog = true">查看详细指南</button>
        <button class="guide-close" @click="guideDismissed = true">&times;</button>
      </div>
    </div>

    <div v-if="loading" class="loading-state"><div class="spinner" /><span>加载中...</span></div>
    <div v-else-if="permissionDenied" class="permission-denied">
      <svg width="48" height="48" viewBox="0 0 48 48" fill="currentColor"><path fill-rule="evenodd" d="M24 4a20 20 0 100 40 20 20 0 000-40zm0 4a16 16 0 110 32 16 16 0 010-32zm-1 8a2 2 0 012 2v6a2 2 0 01-4 0v-6a2 2 0 012-2zm0 12a2 2 0 100 4 2 2 0 000-4z" opacity="0.4" /></svg>
      <h3>权限不足</h3>
      <p>{{ permissionMsg || '您没有访问此功能的权限，请联系管理员开通。' }}</p>
    </div>
    <div v-else-if="agents.length === 0" class="empty-state"><p>暂无自定义 Agent，点击上方按钮创建</p></div>

    <div v-else class="agent-list">
      <div v-for="agent in agents" :key="agent.agent_id" class="agent-card">
        <div class="card-header">
          <div class="agent-icon">{{ agent.icon || '&#x1F916;' }}</div>
          <div class="agent-info">
            <span class="agent-name">{{ agent.display_name || agent.name }}</span>
            <span class="agent-id">{{ agent.name }}</span>
          </div>
          <span class="agent-status" :class="agent.status">{{ statusLabel(agent.status) }}</span>
        </div>
        <p class="agent-desc">{{ agent.description || '暂无描述' }}</p>
        <div class="agent-meta">
          <span class="meta-item">模型: {{ agent.model_tier }}</span>
          <span class="meta-item">温度: {{ agent.temperature }}</span>
          <span class="meta-item">最大轮次: {{ agent.max_rounds }}</span>
          <span class="meta-item">版本: v{{ agent.version }}</span>
        </div>
        <div class="agent-tags">
          <span v-for="tag in agent.tags" :key="tag" class="tag">{{ tag }}</span>
          <span v-if="agent.mcp_servers.length" class="tag mcp">MCP: {{ agent.mcp_servers.length }}</span>
        </div>
        <div class="card-actions">
          <button class="btn-sm btn-edit" @click="openEditDialog(agent)">编辑</button>
          <button v-if="agent.status === 'draft'" class="btn-sm btn-publish" @click="publishAgent(agent.agent_id)">发布</button>
          <button v-if="agent.status === 'published'" class="btn-sm btn-disable" @click="disableAgent(agent.agent_id)">禁用</button>
          <button class="btn-sm btn-versions" @click="showVersions(agent.agent_id)">版本</button>
          <button class="btn-sm btn-danger" @click="deleteAgent(agent.agent_id)">删除</button>
        </div>
      </div>
    </div>

    <el-dialog v-model="showCreateEditDialog" :title="isEdit ? '编辑 Agent' : '创建 Agent'" width="640px" :close-on-click-modal="false">
      <el-form label-width="100px">
        <el-form-item label="名称" required><el-input v-model="form.name" :disabled="isEdit" placeholder="唯一标识符" /></el-form-item>
        <el-form-item label="显示名称"><el-input v-model="form.display_name" placeholder="用户可见名称" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="form.description" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="系统提示词" required><el-input v-model="form.system_prompt" type="textarea" :rows="5" placeholder="Agent 的系统提示词" /></el-form-item>
        <el-form-item label="模型层级">
          <el-select v-model="form.model_tier" style="width: 100%">
            <el-option label="基础 (Lite)" value="lite" />
            <el-option label="标准 (Plus)" value="plus" />
            <el-option label="高级 (Pro)" value="pro" />
          </el-select>
        </el-form-item>
        <el-form-item label="温度"><el-slider v-model="form.temperature" :min="0" :max="2" :step="0.1" show-input /></el-form-item>
        <el-form-item label="最大轮次"><el-input-number v-model="form.max_rounds" :min="1" :max="50" /></el-form-item>
        <el-form-item label="需要审核"><el-switch v-model="form.review_required" /></el-form-item>
        <el-form-item label="MCP 服务器"><el-input v-model="form.mcpServersStr" placeholder="多个用逗号分隔" /></el-form-item>
        <el-form-item label="标签"><el-input v-model="form.tagsStr" placeholder="多个标签用逗号分隔" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateEditDialog = false">取消</el-button>
        <el-button type="primary" @click="submitForm">{{ isEdit ? '保存' : '创建' }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showTemplateDialog" title="从模板创建" width="640px" :close-on-click-modal="false">
      <div v-if="templateLoading" class="loading-state"><div class="spinner" /><span>加载中...</span></div>
      <div v-else-if="templates.length === 0" class="empty-state"><p>暂无可用模板</p></div>
      <div v-else class="template-list">
        <div v-for="tpl in templates" :key="tpl.template_id" class="template-item" @click="selectTemplate(tpl)">
          <div class="tpl-icon">{{ tpl.icon || '&#x1F4CB;' }}</div>
          <div class="tpl-info">
            <span class="tpl-name">{{ tpl.display_name || tpl.name }}</span>
            <p class="tpl-desc">{{ tpl.description }}</p>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="showTemplateDialog = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showVersionDialog" title="版本历史" width="520px" :close-on-click-modal="false">
      <div v-if="versionLoading" class="loading-state"><div class="spinner" /><span>加载中...</span></div>
      <div v-else-if="versions.length === 0" class="empty-state"><p>暂无版本记录</p></div>
      <div v-else class="version-list">
        <div v-for="ver in versions" :key="ver.version" class="version-item">
          <span class="ver-number">v{{ ver.version }}</span>
          <span class="ver-summary">{{ ver.change_summary || '无变更说明' }}</span>
          <span class="ver-time">{{ new Date(ver.created_at * 1000).toLocaleString('zh-CN') }}</span>
          <button class="btn-sm btn-rollback" @click="rollbackVersion(ver.version)">回滚</button>
        </div>
      </div>
      <template #footer>
        <el-button @click="showVersionDialog = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showGuideDialog" title="Agent 构建器使用指南" width="640px">
      <div class="guide-content">
        <div class="guide-section">
          <h4>什么是 Agent？</h4>
          <p>Agent 是具有特定角色和能力的 AI 助手。每个 Agent 都有独立的系统提示词、模型参数和工具配置，可以专注于特定任务场景，如合同审查、数据分析、客服应答等。</p>
        </div>
        <div class="guide-section">
          <h4>使用流程</h4>
          <div class="flow-steps">
            <div class="flow-step">
              <div class="step-num">1</div>
              <div class="step-content">
                <strong>创建 Agent</strong>
                <p>手动创建空白 Agent 或从模板快速创建，配置系统提示词和参数</p>
              </div>
            </div>
            <div class="flow-step">
              <div class="step-num">2</div>
              <div class="step-content">
                <strong>配置参数</strong>
                <p>设置模型层级、温度、最大轮次等参数，绑定 MCP 服务器扩展工具能力</p>
              </div>
            </div>
            <div class="flow-step">
              <div class="step-num">3</div>
              <div class="step-content">
                <strong>发布 Agent</strong>
                <p>配置完成后点击"发布"，发布后的 Agent 可在对话中使用</p>
              </div>
            </div>
            <div class="flow-step">
              <div class="step-num">4</div>
              <div class="step-content">
                <strong>版本管理</strong>
                <p>每次编辑保存会生成新版本，支持查看版本历史和回滚到任意版本</p>
              </div>
            </div>
          </div>
        </div>
        <div class="guide-section">
          <h4>核心配置项</h4>
          <div class="config-list">
            <div class="config-item"><code>系统提示词</code> - 定义 Agent 的角色、行为和能力边界，是最重要的配置</div>
            <div class="config-item"><code>模型层级</code> - Lite(基础)、Plus(标准)、Pro(高级)，层级越高能力越强</div>
            <div class="config-item"><code>温度</code> - 控制输出随机性，0 最确定，2 最随机。推荐 0.3-0.7</div>
            <div class="config-item"><code>最大轮次</code> - Agent 单次对话的最大执行轮数</div>
            <div class="config-item"><code>MCP 服务器</code> - 绑定外部工具服务，扩展 Agent 的操作能力</div>
            <div class="config-item"><code>需要审核</code> - 开启后 Agent 执行高风险操作前需人工审批</div>
          </div>
        </div>
        <div class="guide-section">
          <h4>从模板创建</h4>
          <p>系统内置了常用场景的 Agent 模板（如合同审查助手），选择模板后只需输入名称即可快速创建。模板已预配置好系统提示词和参数，创建后可根据需要进一步调整。</p>
        </div>
        <div class="guide-section">
          <h4>Agent 状态</h4>
          <div class="status-list">
            <div class="status-item"><span class="status-dot draft"></span><strong>草稿</strong> - 未发布，仅可编辑，不可在对话中使用</div>
            <div class="status-item"><span class="status-dot published"></span><strong>已发布</strong> - 已上线，可在对话中选用</div>
            <div class="status-item"><span class="status-dot disabled"></span><strong>已禁用</strong> - 暂时下线，不可使用但保留配置</div>
          </div>
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
import { ElMessage, ElMessageBox } from 'element-plus'
import { agentBuilderApi, type CustomAgent, type AgentTemplate, type AgentVersion } from '../../api/agent-builder'

const loading = ref(false)
const agents = ref<CustomAgent[]>([])
const permissionDenied = ref(false)
const permissionMsg = ref('')

const showCreateEditDialog = ref(false)
const isEdit = ref(false)
const editId = ref('')
const form = reactive({
  name: '', display_name: '', description: '', system_prompt: '',
  model_tier: 'plus', temperature: 0.7, max_rounds: 10,
  review_required: false, mcpServersStr: '', tagsStr: '',
})

const showTemplateDialog = ref(false)
const templateLoading = ref(false)
const templates = ref<AgentTemplate[]>([])

const showVersionDialog = ref(false)
const versionLoading = ref(false)
const versions = ref<AgentVersion[]>([])
const versionAgentId = ref('')
const guideDismissed = ref(false)
const showGuideDialog = ref(false)

function statusLabel(status: string) {
  const map: Record<string, string> = { draft: '草稿', published: '已发布', disabled: '已禁用' }
  return map[status] || status
}

async function loadAgents() {
  loading.value = true
  try { agents.value = await agentBuilderApi.list() || [] }
  catch (e: any) {
    if (e?.name === 'PermissionDenied') { permissionDenied.value = true; permissionMsg.value = e.message }
    agents.value = []
  }
  finally { loading.value = false }
}

async function loadTemplates() {
  showTemplateDialog.value = true
  templateLoading.value = true
  try { templates.value = await agentBuilderApi.templates() || [] }
  catch { templates.value = [] }
  finally { templateLoading.value = false }
}

function openCreateDialog() {
  isEdit.value = false; editId.value = ''
  Object.assign(form, { name: '', display_name: '', description: '', system_prompt: '', model_tier: 'plus', temperature: 0.7, max_rounds: 10, review_required: false, mcpServersStr: '', tagsStr: '' })
  showCreateEditDialog.value = true
}

function openEditDialog(agent: CustomAgent) {
  isEdit.value = true; editId.value = agent.agent_id
  Object.assign(form, {
    name: agent.name, display_name: agent.display_name, description: agent.description,
    system_prompt: agent.system_prompt, model_tier: agent.model_tier,
    temperature: agent.temperature, max_rounds: agent.max_rounds,
    review_required: agent.review_required,
    mcpServersStr: agent.mcp_servers.join(', '), tagsStr: agent.tags.join(', '),
  })
  showCreateEditDialog.value = true
}

async function submitForm() {
  if (!form.name.trim()) { ElMessage.warning('请输入名称'); return }
  if (!form.system_prompt.trim()) { ElMessage.warning('请输入系统提示词'); return }
  const mcp_servers = form.mcpServersStr.split(',').map(s => s.trim()).filter(Boolean)
  const tags = form.tagsStr.split(',').map(s => s.trim()).filter(Boolean)
  const payload = { ...form, mcp_servers, tags }
  try {
    if (isEdit.value) {
      await agentBuilderApi.update(editId.value, payload)
      ElMessage.success('更新成功')
    } else {
      await agentBuilderApi.create(payload)
      ElMessage.success('创建成功')
    }
    showCreateEditDialog.value = false
    loadAgents()
  } catch (e: any) { ElMessage.error(e.message || '操作失败') }
}

async function selectTemplate(tpl: AgentTemplate) {
  try {
    await ElMessageBox.prompt('请输入 Agent 名称', '从模板创建', { inputValue: tpl.name, confirmButtonText: '创建', cancelButtonText: '取消' })
      .then(async ({ value }) => {
        await agentBuilderApi.createFromTemplate(tpl.template_id, { name: value, display_name: tpl.display_name })
        ElMessage.success('创建成功')
        showTemplateDialog.value = false
        loadAgents()
      })
  } catch { /* cancelled */ }
}

async function publishAgent(id: string) {
  try {
    await agentBuilderApi.publish(id)
    ElMessage.success('发布成功')
    loadAgents()
  } catch (e: any) { ElMessage.error(e.message || '发布失败') }
}

async function disableAgent(id: string) {
  try {
    await agentBuilderApi.disable(id)
    ElMessage.success('已禁用')
    loadAgents()
  } catch (e: any) { ElMessage.error(e.message || '操作失败') }
}

async function deleteAgent(id: string) {
  try {
    await ElMessageBox.confirm('确定删除该 Agent？', '删除确认', { type: 'warning' })
    await agentBuilderApi.delete(id)
    ElMessage.success('删除成功')
    loadAgents()
  } catch { /* cancelled */ }
}

async function showVersions(id: string) {
  versionAgentId.value = id
  showVersionDialog.value = true
  versionLoading.value = true
  try { versions.value = await agentBuilderApi.versions(id) || [] }
  catch { versions.value = [] }
  finally { versionLoading.value = false }
}

async function rollbackVersion(version: number) {
  try {
    await ElMessageBox.confirm(`确定回滚到 v${version}？`, '回滚确认')
    await agentBuilderApi.rollback(versionAgentId.value, version)
    ElMessage.success('回滚成功')
    showVersionDialog.value = false
    loadAgents()
  } catch { /* cancelled */ }
}

onMounted(() => { loadAgents() })
</script>

<style scoped>
.agent-builder-page { max-width: 960px; }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
.page-header h2 { font-size: 22px; font-weight: 700; color: var(--color-text); margin: 0; }
.header-actions { display: flex; gap: 10px; }
.btn-refresh { display: flex; align-items: center; gap: 6px; padding: 8px 14px; border-radius: var(--radius-md); color: var(--color-text-secondary); transition: all var(--transition-fast); }
.btn-refresh:hover { background: var(--color-bg); color: var(--color-text); }
.btn-secondary { padding: 8px 18px; border-radius: var(--radius-md); background: var(--color-bg); border: 1px solid var(--color-border-light); color: var(--color-text); font-weight: 500; font-size: 13px; transition: all var(--transition-fast); }
.btn-secondary:hover { border-color: var(--color-primary); color: var(--color-primary); }
.btn-primary { padding: 8px 18px; border-radius: var(--radius-md); background: var(--color-primary); color: white; font-weight: 600; font-size: 13px; transition: all var(--transition-fast); }
.btn-primary:hover { opacity: 0.9; }
.loading-state, .empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 80px 0; color: var(--color-text-tertiary); gap: 12px; font-size: 14px; }
.permission-denied { display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 80px 0; color: var(--color-text-tertiary); gap: 12px; text-align: center; }
.permission-denied h3 { font-size: 18px; font-weight: 700; color: var(--color-text); margin: 8px 0 4px; }
.permission-denied p { font-size: 14px; color: var(--color-text-secondary); max-width: 400px; }
.spinner { width: 28px; height: 28px; border: 3px solid var(--color-border-light); border-top-color: var(--color-primary); border-radius: 50%; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.agent-list { display: flex; flex-direction: column; gap: 16px; }
.agent-card { background: var(--color-bg-elevated); border: 1px solid var(--color-border-light); border-radius: var(--radius-lg); padding: 24px; transition: all var(--transition-fast); }
.agent-card:hover { border-color: var(--color-primary); box-shadow: 0 2px 12px rgba(99,102,241,0.08); }
.card-header { display: flex; align-items: center; gap: 14px; margin-bottom: 12px; }
.agent-icon { width: 44px; height: 44px; display: flex; align-items: center; justify-content: center; background: var(--color-bg); border-radius: var(--radius-md); font-size: 20px; }
.agent-info { flex: 1; }
.agent-name { font-size: 16px; font-weight: 600; color: var(--color-text); display: block; }
.agent-id { font-size: 12px; color: var(--color-text-tertiary); font-family: monospace; }
.agent-status { font-size: 12px; padding: 3px 10px; border-radius: 10px; font-weight: 500; }
.agent-status.draft { background: rgba(234,179,8,0.12); color: #ca8a04; }
.agent-status.published { background: rgba(5,150,105,0.12); color: #059669; }
.agent-status.disabled { background: rgba(107,114,128,0.12); color: #6b7280; }
.agent-desc { font-size: 14px; color: var(--color-text-secondary); margin: 0 0 14px; line-height: 1.6; }
.agent-meta { display: flex; gap: 20px; font-size: 13px; color: var(--color-text-tertiary); margin-bottom: 12px; flex-wrap: wrap; }
.agent-tags { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 16px; }
.tag { font-size: 12px; padding: 3px 10px; border-radius: 8px; background: rgba(99,102,241,0.08); color: var(--color-primary); }
.tag.mcp { background: rgba(168,85,247,0.08); color: #a855f7; }
.card-actions { display: flex; gap: 8px; flex-wrap: wrap; padding-top: 4px; border-top: 1px solid var(--color-border-light); }
.btn-sm { padding: 5px 14px; border-radius: var(--radius-md); font-size: 12px; font-weight: 500; transition: all var(--transition-fast); }
.btn-edit { background: rgba(99,102,241,0.1); color: var(--color-primary); }
.btn-edit:hover { background: rgba(99,102,241,0.2); }
.btn-publish { background: rgba(5,150,105,0.1); color: #059669; }
.btn-publish:hover { background: rgba(5,150,105,0.2); }
.btn-disable { background: rgba(234,179,8,0.1); color: #ca8a04; }
.btn-disable:hover { background: rgba(234,179,8,0.2); }
.btn-versions { background: rgba(6,182,212,0.1); color: #06b6d4; }
.btn-versions:hover { background: rgba(6,182,212,0.2); }
.btn-danger { background: rgba(239,68,68,0.1); color: #ef4444; }
.btn-danger:hover { background: rgba(239,68,68,0.2); }
.btn-rollback { background: rgba(234,179,8,0.1); color: #ca8a04; font-size: 11px; padding: 2px 8px; }
.template-list { display: flex; flex-direction: column; gap: 10px; max-height: 400px; overflow-y: auto; }
.template-item { display: flex; align-items: center; gap: 14px; padding: 16px; background: var(--color-bg); border-radius: var(--radius-md); cursor: pointer; transition: all var(--transition-fast); }
.template-item:hover { border-color: var(--color-primary); background: rgba(99,102,241,0.04); }
.tpl-icon { width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; background: var(--color-bg-elevated); border-radius: var(--radius-md); font-size: 18px; }
.tpl-info { flex: 1; }
.tpl-name { font-size: 14px; font-weight: 600; color: var(--color-text); }
.tpl-desc { font-size: 13px; color: var(--color-text-secondary); margin: 4px 0 0; line-height: 1.5; }
.version-list { display: flex; flex-direction: column; gap: 8px; max-height: 300px; overflow-y: auto; }
.version-item { display: flex; align-items: center; gap: 10px; padding: 10px 12px; background: var(--color-bg); border-radius: var(--radius-md); font-size: 13px; }
.ver-number { font-weight: 600; color: var(--color-primary); min-width: 40px; }
.ver-summary { flex: 1; color: var(--color-text-secondary); }
.ver-time { font-size: 12px; color: var(--color-text-tertiary); }
.btn-outline { padding: 8px 18px; border-radius: var(--radius-md); border: 1px solid var(--color-primary); color: var(--color-primary); font-weight: 600; font-size: 13px; transition: all var(--transition-fast); }
.btn-outline:hover { background: rgba(99,102,241,0.06); }
.guide-banner { background: rgba(99,102,241,0.06); border: 1px solid rgba(99,102,241,0.15); border-radius: var(--radius-lg); padding: 12px 16px; margin-bottom: 16px; }
.guide-banner-content { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--color-text-secondary); line-height: 1.5; }
.guide-icon { width: 22px; height: 22px; border-radius: 50%; background: var(--color-primary); color: white; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; flex-shrink: 0; }
.guide-link { color: var(--color-primary); font-weight: 500; cursor: pointer; white-space: nowrap; }
.guide-link:hover { text-decoration: underline; }
.guide-close { margin-left: auto; color: var(--color-text-tertiary); font-size: 18px; cursor: pointer; padding: 0 4px; line-height: 1; }
.guide-close:hover { color: var(--color-text); }
.guide-content { max-height: 60vh; overflow-y: auto; }
.guide-section { margin-bottom: 20px; }
.guide-section h4 { font-size: 15px; font-weight: 600; color: var(--color-text); margin: 0 0 8px; }
.guide-section p { font-size: 13px; color: var(--color-text-secondary); line-height: 1.6; margin: 0 0 8px; }
.guide-section code { background: rgba(99,102,241,0.08); color: var(--color-primary); padding: 1px 6px; border-radius: 4px; font-size: 12px; }
.flow-steps { display: flex; flex-direction: column; gap: 12px; margin-top: 8px; }
.flow-step { display: flex; gap: 12px; align-items: flex-start; }
.step-num { width: 28px; height: 28px; border-radius: 50%; background: var(--color-primary); color: white; display: flex; align-items: center; justify-content: center; font-size: 13px; font-weight: 700; flex-shrink: 0; }
.step-content strong { font-size: 13px; color: var(--color-text); display: block; margin-bottom: 2px; }
.step-content p { font-size: 12px; color: var(--color-text-tertiary); margin: 0; }
.config-list { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
.config-item { font-size: 13px; color: var(--color-text-secondary); line-height: 1.6; }
.config-item code { background: rgba(99,102,241,0.08); color: var(--color-primary); padding: 1px 6px; border-radius: 4px; font-size: 12px; }
.status-list { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
.status-item { font-size: 13px; color: var(--color-text-secondary); display: flex; align-items: center; gap: 8px; }
.status-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
.status-dot.draft { background: #ca8a04; }
.status-dot.published { background: #059669; }
.status-dot.disabled { background: #6b7280; }
</style>

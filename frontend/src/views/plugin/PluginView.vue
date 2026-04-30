<template>
  <div class="plugin-page">
    <div class="page-header">
      <h2>插件管理</h2>
      <div class="header-actions">
        <button class="btn-refresh" @click="loadPlugins">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path fill-rule="evenodd" d="M8 3a5 5 0 110 10A5 5 0 018 3zm0 1.5a3.5 3.5 0 100 7 3.5 3.5 0 000-7z" opacity="0.4" /><path d="M8 1a7 7 0 016.95 6.25h-1.5A5.5 5.5 0 008 2.5a5.5 5.5 0 00-5.45 4.75h-1.5A7 7 0 018 1z" /></svg>
          刷新
        </button>
        <button class="btn-secondary" @click="loadMarketplace">插件市场</button>
        <button class="btn-primary" @click="showRegisterDialog = true">注册插件</button>
      </div>
    </div>

    <div class="guide-banner" v-if="!guideDismissed">
      <div class="guide-banner-content">
        <span class="guide-icon">?</span>
        <span>插件可以扩展 Agent 的能力，通过 Hook 机制在对话和工具调用的各阶段插入自定义逻辑。启用插件后，Agent 在运行时会自动加载。</span>
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
    <div v-else-if="plugins.length === 0" class="empty-state"><p>暂无插件，点击注册或从市场安装</p></div>

    <div v-else class="plugin-list">
      <div v-for="plugin in plugins" :key="plugin.plugin_id" class="plugin-card">
        <div class="card-header">
          <div class="plugin-icon"><span class="icon-text">{{ getPluginIcon(plugin) }}</span></div>
          <div class="plugin-info">
            <span class="plugin-name">{{ plugin.display_name || plugin.name }}</span>
            <span class="plugin-version">v{{ plugin.version }}</span>
          </div>
          <span class="plugin-status" :class="plugin.status">{{ statusLabel(plugin.status) }}</span>
        </div>
        <p class="plugin-desc">{{ plugin.description || '暂无描述' }}</p>
        <div class="plugin-meta">
          <span class="meta-item">作者: {{ plugin.author || '-' }}</span>
          <span class="meta-item">权限: {{ plugin.permissions.length }}项</span>
          <span class="meta-item">Hook: {{ plugin.hooks.length }}个</span>
        </div>
        <div class="plugin-hooks">
          <span v-for="hook in plugin.hooks" :key="hook" class="hook-tag">{{ hook }}</span>
        </div>
        <div class="card-actions">
          <button v-if="plugin.status !== 'enabled'" class="btn-sm btn-enable" @click="enablePlugin(plugin.plugin_id)">启用</button>
          <button v-if="plugin.status === 'enabled'" class="btn-sm btn-disable" @click="disablePlugin(plugin.plugin_id)">禁用</button>
          <button class="btn-sm btn-danger" @click="unregisterPlugin(plugin.plugin_id)">卸载</button>
        </div>
      </div>
    </div>

    <el-dialog v-model="showMarketDialog" title="插件市场" width="640px" :close-on-click-modal="false">
      <div v-if="marketLoading" class="loading-state"><div class="spinner" /><span>加载中...</span></div>
      <div v-else-if="marketPlugins.length === 0" class="empty-state"><p>暂无可用插件</p></div>
      <div v-else class="market-list">
        <div v-for="mp in marketPlugins" :key="mp.plugin_id" class="market-item">
          <div class="market-info">
            <span class="market-name">{{ mp.display_name || mp.name }}</span>
            <span class="market-version">v{{ mp.version }}</span>
            <p class="market-desc">{{ mp.description }}</p>
          </div>
          <button class="btn-sm btn-install" @click="installPlugin(mp.plugin_id)">安装</button>
        </div>
      </div>
      <template #footer>
        <el-button @click="showMarketDialog = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showRegisterDialog" title="注册插件" width="520px" :close-on-click-modal="false">
      <el-form label-width="90px">
        <el-form-item label="名称" required><el-input v-model="registerForm.name" placeholder="插件唯一标识" /></el-form-item>
        <el-form-item label="显示名称"><el-input v-model="registerForm.display_name" placeholder="用户可见名称" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="registerForm.description" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="版本"><el-input v-model="registerForm.version" placeholder="1.0.0" /></el-form-item>
        <el-form-item label="作者"><el-input v-model="registerForm.author" /></el-form-item>
        <el-form-item label="模块路径"><el-input v-model="registerForm.module_path" placeholder="如: plugins.custom.my_plugin" /></el-form-item>
        <el-form-item label="入口类"><el-input v-model="registerForm.entry_class" placeholder="如: MyPlugin" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showRegisterDialog = false">取消</el-button>
        <el-button type="primary" @click="registerPlugin">注册</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showGuideDialog" title="插件管理使用指南" width="640px">
      <div class="guide-content">
        <div class="guide-section">
          <h4>什么是插件？</h4>
          <p>插件是扩展 Agent 能力的模块化组件。通过 Hook 机制，插件可以在对话和工具调用的各个阶段（如聊天前、聊天后、工具调用前、工具调用后）插入自定义逻辑，实现审计日志、内容过滤、指标采集等功能。</p>
        </div>
        <div class="guide-section">
          <h4>使用流程</h4>
          <div class="flow-steps">
            <div class="flow-step">
              <div class="step-num">1</div>
              <div class="step-content">
                <strong>获取插件</strong>
                <p>从插件市场一键安装官方插件，或手动注册自定义插件</p>
              </div>
            </div>
            <div class="flow-step">
              <div class="step-num">2</div>
              <div class="step-content">
                <strong>启用插件</strong>
                <p>安装后插件处于"已注册"状态，点击"启用"后 Agent 运行时会自动加载该插件</p>
              </div>
            </div>
            <div class="flow-step">
              <div class="step-num">3</div>
              <div class="step-content">
                <strong>验证效果</strong>
                <p>在对话中使用 Agent，插件会根据 Hook 配置自动执行，如记录日志、过滤内容等</p>
              </div>
            </div>
            <div class="flow-step">
              <div class="step-num">4</div>
              <div class="step-content">
                <strong>管理插件</strong>
                <p>可随时禁用或卸载不需要的插件，禁用后 Agent 不再加载该插件</p>
              </div>
            </div>
          </div>
        </div>
        <div class="guide-section">
          <h4>插件状态说明</h4>
          <div class="status-list">
            <div class="status-item"><span class="status-dot registered"></span><strong>已注册</strong> - 插件已安装但未启用，不会生效</div>
            <div class="status-item"><span class="status-dot enabled"></span><strong>已启用</strong> - 插件正在运行，Agent 会自动加载</div>
            <div class="status-item"><span class="status-dot disabled"></span><strong>已禁用</strong> - 插件被手动禁用，不会生效</div>
            <div class="status-item"><span class="status-dot error"></span><strong>异常</strong> - 插件加载或运行出错</div>
          </div>
        </div>
        <div class="guide-section">
          <h4>Hook 挂载点</h4>
          <p>插件通过 Hook 挂载点介入 Agent 运行流程，常用的挂载点包括：</p>
          <div class="hook-list">
            <div class="hook-item"><code>pre_chat</code> - 对话开始前，可修改输入内容</div>
            <div class="hook-item"><code>post_chat</code> - 对话结束后，可处理输出内容</div>
            <div class="hook-item"><code>pre_tool</code> - 工具调用前，可拦截或修改参数</div>
            <div class="hook-item"><code>post_tool</code> - 工具调用后，可处理返回结果</div>
            <div class="hook-item"><code>on_error</code> - 发生错误时，可记录异常信息</div>
          </div>
        </div>
        <div class="guide-section">
          <h4>注册自定义插件</h4>
          <p>如果需要注册自定义插件，需要填写以下关键信息：</p>
          <div class="hook-list">
            <div class="hook-item"><code>名称</code> - 插件唯一标识，如 my-custom-plugin</div>
            <div class="hook-item"><code>模块路径</code> - Python 模块路径，如 plugins.custom.my_plugin</div>
            <div class="hook-item"><code>入口类</code> - 插件主类名，如 MyPlugin</div>
          </div>
          <p style="margin-top: 8px;">自定义插件需要实现对应的 Handler 方法，并放置在项目的 plugins 目录下。</p>
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
import { pluginApi, type PluginManifest } from '../../api/plugin'

const loading = ref(false)
const plugins = ref<PluginManifest[]>([])
const permissionDenied = ref(false)
const permissionMsg = ref('')

const showMarketDialog = ref(false)
const marketLoading = ref(false)
const marketPlugins = ref<PluginManifest[]>([])

const showRegisterDialog = ref(false)
const registerForm = reactive({ name: '', display_name: '', description: '', version: '1.0.0', author: '', module_path: '', entry_class: '' })
const guideDismissed = ref(false)
const showGuideDialog = ref(false)

function statusLabel(status: string) {
  const map: Record<string, string> = { registered: '已注册', enabled: '已启用', disabled: '已禁用', error: '异常' }
  return map[status] || status
}

function getPluginIcon(plugin: PluginManifest): string {
  const icon = plugin.icon || ''
  const emojiRegex = /[\u{1F300}-\u{1F9FF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}\u{FE00}-\u{FE0F}\u{1F000}-\u{1FFFF}]/u
  if (emojiRegex.test(icon)) return icon
  if (plugin.display_name) return plugin.display_name.charAt(0).toUpperCase()
  return plugin.name.charAt(0).toUpperCase()
}

async function loadPlugins() {
  loading.value = true
  try { plugins.value = await pluginApi.list() || [] }
  catch (e: any) {
    if (e?.name === 'PermissionDenied') { permissionDenied.value = true; permissionMsg.value = e.message }
    plugins.value = []
  }
  finally { loading.value = false }
}

async function loadMarketplace() {
  showMarketDialog.value = true
  marketLoading.value = true
  try { marketPlugins.value = await pluginApi.marketplace() || [] }
  catch { marketPlugins.value = [] }
  finally { marketLoading.value = false }
}

async function enablePlugin(id: string) {
  try {
    await pluginApi.enable(id)
    ElMessage.success('插件已启用')
    loadPlugins()
  } catch (e: any) { ElMessage.error(e.message || '启用失败') }
}

async function disablePlugin(id: string) {
  try {
    await pluginApi.disable(id)
    ElMessage.success('插件已禁用')
    loadPlugins()
  } catch (e: any) { ElMessage.error(e.message || '禁用失败') }
}

async function unregisterPlugin(id: string) {
  try {
    await ElMessageBox.confirm('确定卸载该插件？', '卸载确认', { type: 'warning' })
    await pluginApi.unregister(id)
    ElMessage.success('插件已卸载')
    loadPlugins()
  } catch { /* cancelled */ }
}

async function installPlugin(id: string) {
  try {
    await pluginApi.install(id)
    ElMessage.success('安装成功')
    loadPlugins()
  } catch (e: any) { ElMessage.error(e.message || '安装失败') }
}

async function registerPlugin() {
  if (!registerForm.name.trim()) { ElMessage.warning('请输入插件名称'); return }
  try {
    await pluginApi.register(registerForm)
    ElMessage.success('注册成功')
    showRegisterDialog.value = false
    Object.assign(registerForm, { name: '', display_name: '', description: '', version: '1.0.0', author: '', module_path: '', entry_class: '' })
    loadPlugins()
  } catch (e: any) { ElMessage.error(e.message || '注册失败') }
}

onMounted(() => { loadPlugins() })
</script>

<style scoped>
.plugin-page { max-width: 960px; }
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
.plugin-list { display: flex; flex-direction: column; gap: 16px; }
.plugin-card { background: var(--color-bg-elevated); border: 1px solid var(--color-border-light); border-radius: var(--radius-lg); padding: 24px; transition: all var(--transition-fast); }
.plugin-card:hover { border-color: var(--color-primary); box-shadow: 0 2px 12px rgba(99,102,241,0.08); }
.card-header { display: flex; align-items: center; gap: 14px; margin-bottom: 12px; }
.plugin-icon { width: 44px; height: 44px; min-width: 44px; display: flex; align-items: center; justify-content: center; background: var(--color-bg); border-radius: var(--radius-md); overflow: hidden; }
.icon-text { font-size: 18px; font-weight: 700; color: var(--color-primary); line-height: 1; }
.plugin-info { flex: 1; display: flex; align-items: baseline; gap: 8px; }
.plugin-name { font-size: 16px; font-weight: 600; color: var(--color-text); }
.plugin-version { font-size: 12px; color: var(--color-text-tertiary); }
.plugin-status { font-size: 12px; padding: 3px 10px; border-radius: 10px; font-weight: 500; }
.plugin-status.enabled { background: rgba(5,150,105,0.12); color: #059669; }
.plugin-status.registered { background: rgba(99,102,241,0.12); color: var(--color-primary); }
.plugin-status.disabled { background: rgba(107,114,128,0.12); color: #6b7280; }
.plugin-status.error { background: rgba(239,68,68,0.12); color: #ef4444; }
.plugin-desc { font-size: 14px; color: var(--color-text-secondary); margin: 0 0 14px; line-height: 1.6; }
.plugin-meta { display: flex; gap: 20px; font-size: 13px; color: var(--color-text-tertiary); margin-bottom: 12px; }
.plugin-hooks { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 16px; }
.hook-tag { font-size: 12px; padding: 3px 10px; border-radius: 8px; background: rgba(168,85,247,0.08); color: #a855f7; }
.card-actions { display: flex; gap: 8px; padding-top: 4px; border-top: 1px solid var(--color-border-light); }
.btn-sm { padding: 5px 14px; border-radius: var(--radius-md); font-size: 12px; font-weight: 500; transition: all var(--transition-fast); }
.btn-enable { background: rgba(5,150,105,0.1); color: #059669; }
.btn-enable:hover { background: rgba(5,150,105,0.2); }
.btn-disable { background: rgba(234,179,8,0.1); color: #ca8a04; }
.btn-disable:hover { background: rgba(234,179,8,0.2); }
.btn-danger { background: rgba(239,68,68,0.1); color: #ef4444; }
.btn-danger:hover { background: rgba(239,68,68,0.2); }
.btn-install { background: rgba(99,102,241,0.1); color: var(--color-primary); }
.btn-install:hover { background: rgba(99,102,241,0.2); }
.market-list { display: flex; flex-direction: column; gap: 12px; max-height: 400px; overflow-y: auto; }
.market-item { display: flex; justify-content: space-between; align-items: center; padding: 16px; background: var(--color-bg); border-radius: var(--radius-md); }
.market-info { flex: 1; }
.market-name { font-size: 14px; font-weight: 600; color: var(--color-text); }
.market-version { font-size: 12px; color: var(--color-text-tertiary); margin-left: 6px; }
.market-desc { font-size: 13px; color: var(--color-text-secondary); margin: 6px 0 0; line-height: 1.5; }
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
.status-list { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
.status-item { font-size: 13px; color: var(--color-text-secondary); display: flex; align-items: center; gap: 8px; }
.status-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
.status-dot.registered { background: var(--color-primary); }
.status-dot.enabled { background: #059669; }
.status-dot.disabled { background: #6b7280; }
.status-dot.error { background: #ef4444; }
.hook-list { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
.hook-item { font-size: 13px; color: var(--color-text-secondary); line-height: 1.6; }
.hook-item code { background: rgba(99,102,241,0.08); color: var(--color-primary); padding: 1px 6px; border-radius: 4px; font-size: 12px; }
</style>

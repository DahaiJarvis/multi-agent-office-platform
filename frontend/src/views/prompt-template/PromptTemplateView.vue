<template>
  <div class="prompt-page">
    <div class="page-header">
      <h2>Prompt 模板库</h2>
      <div class="header-actions">
        <button class="btn-refresh" @click="loadTemplates">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path fill-rule="evenodd" d="M8 3a5 5 0 110 10A5 5 0 018 3zm0 1.5a3.5 3.5 0 100 7 3.5 3.5 0 000-7z" opacity="0.4" /><path d="M8 1a7 7 0 016.95 6.25h-1.5A5.5 5.5 0 008 2.5a5.5 5.5 0 00-5.45 4.75h-1.5A7 7 0 018 1z" /></svg>
          刷新
        </button>
        <button class="btn-primary" @click="openCreateDialog">创建模板</button>
      </div>
    </div>

    <div class="guide-banner" v-if="!guideDismissed">
      <div class="guide-banner-content">
        <span class="guide-icon">?</span>
        <span>Prompt 模板库用于管理和复用提示词。支持变量插值、分类筛选和评分，让提示词编写更高效、更规范。</span>
        <button class="guide-link" @click="showGuideDialog = true">查看详细指南</button>
        <button class="guide-close" @click="guideDismissed = true">&times;</button>
      </div>
    </div>

    <div class="filter-bar">
      <select v-model="categoryFilter" class="filter-select" @change="loadTemplates">
        <option value="">全部分类</option>
        <option value="system">系统提示</option>
        <option value="task">任务指令</option>
        <option value="conversation">对话模板</option>
        <option value="analysis">分析模板</option>
        <option value="writing">写作模板</option>
        <option value="custom">自定义</option>
      </select>
      <input v-model="keywordFilter" class="filter-input" placeholder="搜索模板..." @keydown.enter="loadTemplates" />
      <button class="btn-search" @click="loadTemplates">搜索</button>
    </div>

    <div v-if="loading" class="loading-state"><div class="spinner" /><span>加载中...</span></div>
    <div v-else-if="templates.length === 0" class="empty-state"><p>暂无模板，点击上方按钮创建</p></div>

    <div v-else class="template-grid">
      <div v-for="tpl in templates" :key="tpl.template_id" class="template-card">
        <div class="card-header">
          <span class="tpl-name">{{ tpl.name }}</span>
          <span class="tpl-category" :class="tpl.category">{{ categoryLabel(tpl.category) }}</span>
        </div>
        <p class="tpl-desc">{{ tpl.description || '暂无描述' }}</p>
        <div class="tpl-preview">{{ tpl.template.slice(0, 120) }}{{ tpl.template.length > 120 ? '...' : '' }}</div>
        <div class="tpl-meta">
          <span class="meta-item">评分: {{ tpl.rating.toFixed(1) }}</span>
          <span class="meta-item">使用: {{ tpl.usage_count }}次</span>
          <span class="meta-item">{{ tpl.is_public ? '公开' : '私有' }}</span>
        </div>
        <div class="tpl-tags">
          <span v-for="tag in tpl.tags" :key="tag" class="tag">{{ tag }}</span>
        </div>
        <div class="card-actions">
          <button class="btn-sm btn-render" @click="openRenderDialog(tpl)">渲染</button>
          <button class="btn-sm btn-rate" @click="rateTemplate(tpl)">评分</button>
          <button class="btn-sm btn-edit" @click="openEditDialog(tpl)">编辑</button>
          <button class="btn-sm btn-danger" @click="deleteTemplate(tpl.template_id)">删除</button>
        </div>
      </div>
    </div>

    <el-dialog v-model="showCreateEditDialog" :title="isEdit ? '编辑模板' : '创建模板'" width="640px" :close-on-click-modal="false">
      <el-form label-width="90px">
        <el-form-item label="名称" required><el-input v-model="form.name" placeholder="模板名称" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="form.description" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="分类">
          <el-select v-model="form.category" style="width: 100%">
            <el-option label="系统提示" value="system" />
            <el-option label="任务指令" value="task" />
            <el-option label="对话模板" value="conversation" />
            <el-option label="分析模板" value="analysis" />
            <el-option label="写作模板" value="writing" />
            <el-option label="自定义" value="custom" />
          </el-select>
        </el-form-item>
        <el-form-item label="模板内容" required><el-input v-model="form.template" type="textarea" :rows="6" placeholder="使用 {{变量名}} 定义变量" /></el-form-item>
        <el-form-item label="标签"><el-input v-model="form.tagsStr" placeholder="多个标签用逗号分隔" /></el-form-item>
        <el-form-item label="公开"><el-switch v-model="form.is_public" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateEditDialog = false">取消</el-button>
        <el-button type="primary" @click="submitForm">{{ isEdit ? '保存' : '创建' }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showRenderDialog" title="渲染模板" width="640px" :close-on-click-modal="false">
      <div class="render-section">
        <h4>变量填写</h4>
        <div v-if="renderVars.length === 0" class="empty-hint">此模板无变量</div>
        <div v-else class="var-list">
          <div v-for="v in renderVars" :key="v.name" class="var-item">
            <label class="var-label">{{ v.name }} <span v-if="v.description" class="var-desc">- {{ v.description }}</span></label>
            <el-input v-model="renderVarValues[v.name]" :placeholder="v.default_value || v.name" />
          </div>
        </div>
      </div>
      <div class="render-section">
        <h4>渲染结果</h4>
        <div v-if="renderedText" class="render-result"><pre>{{ renderedText }}</pre></div>
        <div v-else class="empty-hint">点击下方按钮渲染</div>
      </div>
      <template #footer>
        <el-button @click="showRenderDialog = false">关闭</el-button>
        <el-button type="primary" @click="doRender">渲染</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showGuideDialog" title="Prompt 模板使用指南" width="640px">
      <div class="guide-content">
        <div class="guide-section">
          <h4>什么是 Prompt 模板？</h4>
          <p>Prompt 模板是可复用的提示词片段，支持变量插值。通过模板化管理，可以避免重复编写相似的提示词，保持提示词的一致性和规范性。</p>
        </div>
        <div class="guide-section">
          <h4>使用流程</h4>
          <div class="flow-steps">
            <div class="flow-step">
              <div class="step-num">1</div>
              <div class="step-content">
                <strong>创建模板</strong>
                <p>编写模板内容，使用 {{ 变量名 }} 语法定义可替换的变量</p>
              </div>
            </div>
            <div class="flow-step">
              <div class="step-num">2</div>
              <div class="step-content">
                <strong>分类管理</strong>
                <p>选择合适的分类（系统提示、任务指令、对话模板等），添加标签便于检索</p>
              </div>
            </div>
            <div class="flow-step">
              <div class="step-num">3</div>
              <div class="step-content">
                <strong>渲染使用</strong>
                <p>点击"渲染"按钮，填入变量值后生成最终的提示词文本</p>
              </div>
            </div>
            <div class="flow-step">
              <div class="step-num">4</div>
              <div class="step-content">
                <strong>评分反馈</strong>
                <p>对模板效果进行评分，帮助团队筛选出高质量的提示词</p>
              </div>
            </div>
          </div>
        </div>
        <div class="guide-section">
          <h4>变量语法</h4>
          <p>在模板内容中使用双花括号定义变量：<code>{{ 变量名 }}</code>。渲染时系统会自动识别所有变量，并提示你填入具体值。</p>
          <p>示例：模板内容为 <code>请帮我分析{{行业}}的{{指标}}数据</code>，渲染时填入行业="电商"、指标="转化率"，即可生成 <code>请帮我分析电商的转化率数据</code>。</p>
        </div>
        <div class="guide-section">
          <h4>模板分类</h4>
          <div class="config-list">
            <div class="config-item"><code>系统提示</code> - Agent 的系统级提示词，定义角色和行为</div>
            <div class="config-item"><code>任务指令</code> - 具体任务的指令模板</div>
            <div class="config-item"><code>对话模板</code> - 对话场景的提示词模板</div>
            <div class="config-item"><code>分析模板</code> - 数据分析类提示词</div>
            <div class="config-item"><code>写作模板</code> - 内容创作类提示词</div>
            <div class="config-item"><code>自定义</code> - 其他类型的提示词</div>
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
import { promptTemplateApi, type PromptTemplate } from '../../api/prompt-template'

const loading = ref(false)
const templates = ref<PromptTemplate[]>([])
const categoryFilter = ref('')
const keywordFilter = ref('')

const showCreateEditDialog = ref(false)
const isEdit = ref(false)
const editId = ref('')
const form = reactive({ name: '', description: '', category: 'custom', template: '', tagsStr: '', is_public: true })

const showRenderDialog = ref(false)
const renderTarget = ref<PromptTemplate | null>(null)
const renderVars = ref<Array<{ name: string; description: string; default_value: string; required: boolean }>>([])
const renderVarValues = reactive<Record<string, string>>({})
const renderedText = ref('')
const guideDismissed = ref(false)
const showGuideDialog = ref(false)

function categoryLabel(cat: string) {
  const map: Record<string, string> = { system: '系统提示', task: '任务指令', conversation: '对话模板', analysis: '分析模板', writing: '写作模板', custom: '自定义' }
  return map[cat] || cat
}

async function loadTemplates() {
  loading.value = true
  try { templates.value = await promptTemplateApi.list({ category: categoryFilter.value || undefined, keyword: keywordFilter.value || undefined }) || [] }
  catch { templates.value = [] }
  finally { loading.value = false }
}

function openCreateDialog() {
  isEdit.value = false; editId.value = ''
  Object.assign(form, { name: '', description: '', category: 'custom', template: '', tagsStr: '', is_public: true })
  showCreateEditDialog.value = true
}

function openEditDialog(tpl: PromptTemplate) {
  isEdit.value = true; editId.value = tpl.template_id
  Object.assign(form, { name: tpl.name, description: tpl.description, category: tpl.category, template: tpl.template, tagsStr: tpl.tags.join(', '), is_public: tpl.is_public })
  showCreateEditDialog.value = true
}

async function submitForm() {
  if (!form.name.trim()) { ElMessage.warning('请输入名称'); return }
  if (!form.template.trim()) { ElMessage.warning('请输入模板内容'); return }
  const tags = form.tagsStr.split(',').map(s => s.trim()).filter(Boolean)
  try {
    if (isEdit.value) {
      await promptTemplateApi.update(editId.value, { name: form.name, description: form.description, category: form.category, template: form.template, tags, is_public: form.is_public })
      ElMessage.success('更新成功')
    } else {
      await promptTemplateApi.create({ name: form.name, description: form.description, category: form.category as any, template: form.template, tags, is_public: form.is_public })
      ElMessage.success('创建成功')
    }
    showCreateEditDialog.value = false
    loadTemplates()
  } catch (e: any) { ElMessage.error(e.message || '操作失败') }
}

async function deleteTemplate(id: string) {
  try {
    await ElMessageBox.confirm('确定删除该模板？', '删除确认', { type: 'warning' })
    await promptTemplateApi.delete(id)
    ElMessage.success('删除成功')
    loadTemplates()
  } catch { /* cancelled */ }
}

function openRenderDialog(tpl: PromptTemplate) {
  renderTarget.value = tpl
  renderVars.value = tpl.variables || []
  Object.keys(renderVarValues).forEach(k => delete renderVarValues[k])
  renderVars.value.forEach(v => { renderVarValues[v.name] = v.default_value || '' })
  renderedText.value = ''
  showRenderDialog.value = true
}

async function doRender() {
  if (!renderTarget.value) return
  try {
    const result = await promptTemplateApi.render(renderTarget.value.template_id, { ...renderVarValues })
    renderedText.value = result?.rendered_text || ''
  } catch (e: any) { ElMessage.error(e.message || '渲染失败') }
}

async function rateTemplate(tpl: PromptTemplate) {
  try {
    const { value } = await ElMessageBox.prompt('请输入评分 (1-5)', '评分', { inputPattern: /^[1-5](\.[0-9])?$/, inputErrorMessage: '请输入 1-5 之间的评分', inputValue: String(tpl.rating) })
    await promptTemplateApi.rate(tpl.template_id, parseFloat(value))
    ElMessage.success('评分成功')
    loadTemplates()
  } catch { /* cancelled */ }
}

onMounted(() => { loadTemplates() })
</script>

<style scoped>
.prompt-page { max-width: 960px; }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
.page-header h2 { font-size: 22px; font-weight: 700; color: var(--color-text); margin: 0; }
.header-actions { display: flex; gap: 10px; }
.btn-refresh { display: flex; align-items: center; gap: 6px; padding: 8px 14px; border-radius: var(--radius-md); color: var(--color-text-secondary); transition: all var(--transition-fast); }
.btn-refresh:hover { background: var(--color-bg); color: var(--color-text); }
.btn-primary { padding: 8px 18px; border-radius: var(--radius-md); background: var(--color-primary); color: white; font-weight: 600; font-size: 13px; transition: all var(--transition-fast); }
.btn-primary:hover { opacity: 0.9; }
.filter-bar { display: flex; gap: 10px; margin-bottom: 20px; }
.filter-select { padding: 8px 12px; border-radius: var(--radius-md); border: 1px solid var(--color-border-light); background: var(--color-bg-elevated); color: var(--color-text); font-size: 13px; }
.filter-input { flex: 1; max-width: 300px; padding: 8px 12px; border-radius: var(--radius-md); border: 1px solid var(--color-border-light); background: var(--color-bg-elevated); color: var(--color-text); font-size: 13px; }
.btn-search { padding: 8px 14px; border-radius: var(--radius-md); background: var(--color-bg); border: 1px solid var(--color-border-light); color: var(--color-text); font-size: 13px; transition: all var(--transition-fast); }
.btn-search:hover { border-color: var(--color-primary); color: var(--color-primary); }
.loading-state, .empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 80px 0; color: var(--color-text-tertiary); gap: 12px; font-size: 14px; }
.spinner { width: 28px; height: 28px; border: 3px solid var(--color-border-light); border-top-color: var(--color-primary); border-radius: 50%; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.template-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }
.template-card { background: var(--color-bg-elevated); border: 1px solid var(--color-border-light); border-radius: var(--radius-lg); padding: 24px; transition: all var(--transition-fast); }
.template-card:hover { border-color: var(--color-primary); box-shadow: 0 2px 12px rgba(99,102,241,0.08); }
.card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.tpl-name { font-size: 16px; font-weight: 600; color: var(--color-text); }
.tpl-category { font-size: 12px; padding: 3px 10px; border-radius: 8px; font-weight: 500; }
.tpl-category.system { background: rgba(99,102,241,0.12); color: var(--color-primary); }
.tpl-category.task { background: rgba(5,150,105,0.12); color: #059669; }
.tpl-category.conversation { background: rgba(6,182,212,0.12); color: #06b6d4; }
.tpl-category.analysis { background: rgba(168,85,247,0.12); color: #a855f7; }
.tpl-category.writing { background: rgba(249,115,22,0.12); color: #f97316; }
.tpl-category.custom { background: rgba(107,114,128,0.12); color: #6b7280; }
.tpl-desc { font-size: 14px; color: var(--color-text-secondary); margin-bottom: 12px; line-height: 1.6; }
.tpl-preview { font-size: 13px; color: var(--color-text-tertiary); font-family: monospace; background: var(--color-bg); padding: 12px; border-radius: var(--radius-md); margin-bottom: 14px; white-space: pre-wrap; word-break: break-all; max-height: 80px; overflow: hidden; line-height: 1.5; }
.tpl-meta { display: flex; gap: 16px; font-size: 13px; color: var(--color-text-tertiary); margin-bottom: 10px; }
.tpl-tags { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 16px; }
.tag { font-size: 12px; padding: 3px 10px; border-radius: 6px; background: rgba(99,102,241,0.08); color: var(--color-primary); }
.card-actions { display: flex; gap: 8px; flex-wrap: wrap; padding-top: 4px; border-top: 1px solid var(--color-border-light); }
.btn-sm { padding: 5px 14px; border-radius: var(--radius-md); font-size: 12px; font-weight: 500; transition: all var(--transition-fast); }
.btn-render { background: rgba(6,182,212,0.1); color: #06b6d4; }
.btn-render:hover { background: rgba(6,182,212,0.2); }
.btn-rate { background: rgba(234,179,8,0.1); color: #ca8a04; }
.btn-rate:hover { background: rgba(234,179,8,0.2); }
.btn-edit { background: rgba(99,102,241,0.1); color: var(--color-primary); }
.btn-edit:hover { background: rgba(99,102,241,0.2); }
.btn-danger { background: rgba(239,68,68,0.1); color: #ef4444; }
.btn-danger:hover { background: rgba(239,68,68,0.2); }
.render-section { margin-bottom: 20px; }
.render-section h4 { font-size: 14px; font-weight: 600; color: var(--color-text); margin-bottom: 10px; }
.empty-hint { font-size: 13px; color: var(--color-text-tertiary); }
.var-list { display: flex; flex-direction: column; gap: 10px; }
.var-item { display: flex; flex-direction: column; gap: 6px; }
.var-label { font-size: 13px; font-weight: 500; color: var(--color-text); }
.var-desc { font-weight: 400; color: var(--color-text-tertiary); }
.render-result { background: var(--color-bg); padding: 16px; border-radius: var(--radius-md); }
.render-result pre { margin: 0; font-size: 13px; color: var(--color-text); white-space: pre-wrap; word-break: break-all; line-height: 1.6; }
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
</style>

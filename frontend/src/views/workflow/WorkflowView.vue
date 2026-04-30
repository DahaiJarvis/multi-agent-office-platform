<template>
  <div class="workflow-page">
    <div class="page-header">
      <h2>工作流管理</h2>
      <div class="header-actions">
        <button class="btn-outline" @click="showTemplateDialog = true">从模板创建</button>
        <button class="btn-refresh" @click="loadWorkflows">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path fill-rule="evenodd" d="M8 3a5 5 0 110 10A5 5 0 018 3zm0 1.5a3.5 3.5 0 100 7 3.5 3.5 0 000-7z" opacity="0.4" /><path d="M8 1a7 7 0 016.95 6.25h-1.5A5.5 5.5 0 008 2.5a5.5 5.5 0 00-5.45 4.75h-1.5A7 7 0 018 1z" /></svg>
          刷新
        </button>
        <button class="btn-primary" @click="openCreateDialog">创建工作流</button>
      </div>
    </div>

    <div class="guide-banner" v-if="!guideDismissed">
      <div class="guide-banner-content">
        <span class="guide-icon">?</span>
        <span>工作流将多个 Agent 和工具按流程编排，实现复杂任务的自动化执行。支持条件分支、并行处理和人工审批等节点类型。</span>
        <button class="guide-link" @click="showGuideDialog = true">查看详细指南</button>
        <button class="guide-close" @click="guideDismissed = true">&times;</button>
      </div>
    </div>

    <div class="filter-bar">
      <select v-model="statusFilter" class="filter-select" @change="loadWorkflows">
        <option value="">全部状态</option>
        <option value="draft">草稿</option>
        <option value="published">已发布</option>
        <option value="disabled">已禁用</option>
      </select>
    </div>

    <div v-if="loading" class="loading-state"><div class="spinner" /><span>加载中...</span></div>
    <div v-else-if="workflows.length === 0" class="empty-state">
      <p>暂无工作流</p>
      <p class="empty-hint">可以从模板快速创建，也可以手动创建空白工作流</p>
      <button class="btn-primary" @click="showTemplateDialog = true">从模板创建</button>
    </div>

    <div v-else class="workflow-list">
      <div v-for="wf in workflows" :key="wf.workflow_id" class="workflow-card" @click="openDetail(wf)">
        <div class="card-header">
          <span class="wf-name">{{ wf.name }}</span>
          <span class="wf-status" :class="wf.status">{{ statusLabel(wf.status) }}</span>
        </div>
        <p class="wf-desc">{{ wf.description || '暂无描述' }}</p>
        <div class="card-meta">
          <span class="meta-item">节点: {{ wf.nodes.length }}</span>
          <span class="meta-item">连接: {{ wf.edges.length }}</span>
          <span class="meta-item">版本: v{{ wf.version }}</span>
          <span class="meta-item">{{ formatTime(wf.updated_at) }}</span>
        </div>
        <div class="card-tags">
          <span v-for="tag in wf.tags" :key="tag" class="tag">{{ tag }}</span>
        </div>
        <div class="card-actions" @click.stop>
          <button v-if="wf.status === 'draft'" class="btn-sm btn-publish" @click="publishWorkflow(wf.workflow_id)">发布</button>
          <button class="btn-sm btn-execute" @click="executeWorkflow(wf.workflow_id)">执行</button>
          <button class="btn-sm btn-danger" @click="deleteWorkflow(wf.workflow_id)">删除</button>
        </div>
      </div>
    </div>

    <el-dialog v-model="showCreateDialog" title="创建工作流" width="480px" :close-on-click-modal="false">
      <el-form label-width="80px">
        <el-form-item label="名称"><el-input v-model="createForm.name" placeholder="请输入工作流名称" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="createForm.description" type="textarea" :rows="3" placeholder="请输入描述" /></el-form-item>
        <el-form-item label="标签"><el-input v-model="createForm.tagsStr" placeholder="多个标签用逗号分隔" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" @click="createWorkflow">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showTemplateDialog" title="选择工作流模板" width="720px" :close-on-click-modal="false">
      <div class="template-grid">
        <div v-for="tpl in workflowTemplates" :key="tpl.id" class="template-card" @click="selectTemplate(tpl)">
          <div class="tpl-icon" :style="{ background: tpl.color }">
            <span class="tpl-icon-text">{{ tpl.icon }}</span>
          </div>
          <div class="tpl-info">
            <h4>{{ tpl.name }}</h4>
            <p>{{ tpl.description }}</p>
            <div class="tpl-meta">
              <span>{{ tpl.nodes.length }} 个节点</span>
              <span>{{ tpl.edges.length }} 个连接</span>
            </div>
          </div>
          <div class="tpl-action">
            <button class="btn-sm btn-use">使用</button>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="showTemplateDialog = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showTemplateConfirmDialog" title="从模板创建工作流" width="480px" :close-on-click-modal="false">
      <div v-if="selectedTemplate" class="template-confirm">
        <p class="confirm-hint">将基于模板 <strong>{{ selectedTemplate.name }}</strong> 创建工作流，你可以修改名称和描述：</p>
        <el-form label-width="80px" style="margin-top: 16px;">
          <el-form-item label="名称"><el-input v-model="templateForm.name" /></el-form-item>
          <el-form-item label="描述"><el-input v-model="templateForm.description" type="textarea" :rows="3" /></el-form-item>
        </el-form>
      </div>
      <template #footer>
        <el-button @click="showTemplateConfirmDialog = false">取消</el-button>
        <el-button type="primary" @click="createFromTemplate">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showDetailDialog" :title="detailData?.name || '工作流详情'" width="760px" :close-on-click-modal="false">
      <div v-if="detailData" class="detail-content">
        <div class="detail-toolbar">
          <div class="detail-meta">
            <span>状态: <strong :class="detailData.status">{{ statusLabel(detailData.status) }}</strong></span>
            <span>版本: v{{ detailData.version }}</span>
            <span>创建者: {{ detailData.created_by || '-' }}</span>
          </div>
          <div class="detail-actions">
            <button v-if="!isEditingDetail" class="btn-sm btn-edit" @click="startEditDetail">编辑</button>
            <template v-else>
              <button class="btn-sm btn-save" @click="saveEditDetail">保存</button>
              <button class="btn-sm btn-cancel" @click="cancelEditDetail">取消</button>
            </template>
          </div>
        </div>

        <template v-if="!isEditingDetail">
          <p class="detail-desc">{{ detailData.description || '暂无描述' }}</p>
          <div v-if="detailData.tags?.length" class="detail-tags">
            <span v-for="tag in detailData.tags" :key="tag" class="tag">{{ tag }}</span>
          </div>
        </template>
        <template v-else>
          <div class="edit-form">
            <div class="edit-row">
              <label>名称</label>
              <el-input v-model="editForm.name" />
            </div>
            <div class="edit-row">
              <label>描述</label>
              <el-input v-model="editForm.description" type="textarea" :rows="2" />
            </div>
            <div class="edit-row">
              <label>标签</label>
              <el-input v-model="editForm.tagsStr" placeholder="多个标签用逗号分隔" />
            </div>
          </div>
        </template>

        <div class="section-header">
          <h4>节点列表 ({{ detailData.nodes.length }})</h4>
          <button v-if="isEditingDetail" class="btn-sm btn-add" @click="showAddNodeForm = true">添加节点</button>
        </div>
        <div v-if="detailData.nodes.length === 0" class="empty-hint">暂无节点</div>
        <div v-else class="node-list">
          <div v-for="node in detailData.nodes" :key="node.node_id" class="node-item">
            <span class="node-type-badge" :class="node.type">{{ node.type }}</span>
            <span class="node-name">{{ node.name || node.node_id }}</span>
            <span v-if="node.agent_name" class="node-agent">{{ node.agent_name }}</span>
            <span v-if="node.tool_name" class="node-agent">{{ node.tool_name }}</span>
            <button v-if="isEditingDetail" class="btn-sm btn-remove" @click="removeNode(node.node_id)">移除</button>
          </div>
        </div>

        <div class="section-header">
          <h4>连接列表 ({{ detailData.edges.length }})</h4>
          <button v-if="isEditingDetail" class="btn-sm btn-add" @click="showAddEdgeForm = true">添加连接</button>
        </div>
        <div v-if="detailData.edges.length === 0" class="empty-hint">暂无连接</div>
        <div v-else class="edge-list">
          <div v-for="edge in detailData.edges" :key="edge.edge_id" class="edge-item">
            <span class="edge-node">{{ edge.source_node_id }}</span>
            <span class="edge-arrow">&rarr;</span>
            <span class="edge-node">{{ edge.target_node_id }}</span>
            <span v-if="edge.label" class="edge-label">{{ edge.label }}</span>
            <button v-if="isEditingDetail" class="btn-sm btn-remove" @click="removeEdge(edge.edge_id)">移除</button>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="closeDetailDialog">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showAddNodeForm" title="添加节点" width="480px" :close-on-click-modal="false" append-to-body>
      <el-form label-width="80px">
        <el-form-item label="类型" required>
          <el-select v-model="addNodeForm.type" style="width: 100%">
            <el-option label="Agent 节点" value="agent" />
            <el-option label="条件节点" value="condition" />
            <el-option label="并行节点" value="parallel" />
            <el-option label="工具节点" value="tool" />
            <el-option label="转换节点" value="transform" />
            <el-option label="人工输入" value="human_input" />
            <el-option label="延迟节点" value="delay" />
            <el-option label="起始节点" value="start" />
            <el-option label="结束节点" value="end" />
          </el-select>
        </el-form-item>
        <el-form-item label="名称"><el-input v-model="addNodeForm.name" placeholder="节点名称" /></el-form-item>
        <el-form-item v-if="addNodeForm.type === 'agent'" label="Agent"><el-input v-model="addNodeForm.agent_name" placeholder="Agent 名称" /></el-form-item>
        <el-form-item v-if="addNodeForm.type === 'tool'" label="工具"><el-input v-model="addNodeForm.tool_name" placeholder="工具名称" /></el-form-item>
        <el-form-item v-if="addNodeForm.type === 'condition'" label="条件表达式"><el-input v-model="addNodeForm.condition_expr" placeholder="条件表达式" /></el-form-item>
        <el-form-item v-if="addNodeForm.type === 'delay'" label="延迟秒数"><el-input-number v-model="addNodeForm.delay_seconds" :min="1" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAddNodeForm = false">取消</el-button>
        <el-button type="primary" @click="doAddNode">添加</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showAddEdgeForm" title="添加连接" width="480px" :close-on-click-modal="false" append-to-body>
      <el-form label-width="80px">
        <el-form-item label="源节点" required>
          <el-select v-model="addEdgeForm.source_node_id" style="width: 100%">
            <el-option v-for="n in detailData?.nodes || []" :key="n.node_id" :label="n.name || n.node_id" :value="n.node_id" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标节点" required>
          <el-select v-model="addEdgeForm.target_node_id" style="width: 100%">
            <el-option v-for="n in detailData?.nodes || []" :key="n.node_id" :label="n.name || n.node_id" :value="n.node_id" />
          </el-select>
        </el-form-item>
        <el-form-item label="标签"><el-input v-model="addEdgeForm.label" placeholder="连接标签（可选）" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAddEdgeForm = false">取消</el-button>
        <el-button type="primary" @click="doAddEdge">添加</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showExecDialog" title="执行工作流" width="480px" :close-on-click-modal="false">
      <el-form label-width="80px">
        <el-form-item label="输入数据">
          <el-input v-model="execInput" type="textarea" :rows="4" placeholder='JSON 格式，如: {"key": "value"}' />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showExecDialog = false">取消</el-button>
        <el-button type="primary" @click="doExecute">执行</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showGuideDialog" title="工作流使用指南" width="640px">
      <div class="guide-content">
        <div class="guide-section">
          <h4>什么是工作流？</h4>
          <p>工作流将多个 Agent 和工具按流程编排，实现复杂任务的自动化执行。通过定义节点和连接，可以构建包含条件分支、并行处理、人工审批等逻辑的自动化流程。</p>
        </div>
        <div class="guide-section">
          <h4>使用流程</h4>
          <div class="flow-steps">
            <div class="flow-step">
              <div class="step-num">1</div>
              <div class="step-content">
                <strong>创建工作流</strong>
                <p>手动创建空白工作流或从模板快速创建，填写名称和描述</p>
              </div>
            </div>
            <div class="flow-step">
              <div class="step-num">2</div>
              <div class="step-content">
                <strong>编排节点</strong>
                <p>添加 Agent 节点、条件节点、工具节点等，配置节点参数和执行顺序</p>
              </div>
            </div>
            <div class="flow-step">
              <div class="step-num">3</div>
              <div class="step-content">
                <strong>连接节点</strong>
                <p>通过连接定义节点的执行顺序和数据流向，支持条件分支</p>
              </div>
            </div>
            <div class="flow-step">
              <div class="step-num">4</div>
              <div class="step-content">
                <strong>发布执行</strong>
                <p>发布工作流后，可在对话中触发或通过 API 调用自动执行</p>
              </div>
            </div>
          </div>
        </div>
        <div class="guide-section">
          <h4>节点类型</h4>
          <div class="config-list">
            <div class="config-item"><code>Agent 节点</code> - 调用指定 Agent 执行任务</div>
            <div class="config-item"><code>条件节点</code> - 根据条件表达式选择不同分支</div>
            <div class="config-item"><code>并行节点</code> - 同时执行多个分支</div>
            <div class="config-item"><code>工具节点</code> - 调用指定工具执行操作</div>
            <div class="config-item"><code>转换节点</code> - 对数据进行格式转换</div>
            <div class="config-item"><code>人工输入</code> - 暂停等待人工输入后继续</div>
            <div class="config-item"><code>延迟节点</code> - 等待指定时间后继续</div>
            <div class="config-item"><code>起始/结束</code> - 工作流的入口和出口</div>
          </div>
        </div>
        <div class="guide-section">
          <h4>编辑工作流</h4>
          <p>点击工作流卡片查看详情，在详情弹窗中点击"编辑"按钮进入编辑模式。可以修改名称、描述、标签，添加或移除节点和连接。编辑完成后点击"保存"提交更改。</p>
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
import { workflowApi, type Workflow } from '../../api/workflow'

const loading = ref(false)
const workflows = ref<Workflow[]>([])
const statusFilter = ref('')

const showCreateDialog = ref(false)
const createForm = reactive({ name: '', description: '', tagsStr: '' })

const showDetailDialog = ref(false)
const detailData = ref<Workflow | null>(null)
const isEditingDetail = ref(false)
const editForm = reactive({ name: '', description: '', tagsStr: '' })
const showAddNodeForm = ref(false)
const addNodeForm = reactive({ type: 'agent', name: '', agent_name: '', tool_name: '', condition_expr: '', delay_seconds: 5 })
const showAddEdgeForm = ref(false)
const addEdgeForm = reactive({ source_node_id: '', target_node_id: '', label: '' })

const showExecDialog = ref(false)
const execWorkflowId = ref('')
const execInput = ref('{}')

const showTemplateDialog = ref(false)
const showTemplateConfirmDialog = ref(false)
const guideDismissed = ref(false)
const showGuideDialog = ref(false)
const selectedTemplate = ref<WorkflowTemplate | null>(null)
const templateForm = reactive({ name: '', description: '' })

interface WorkflowTemplate {
  id: string
  name: string
  description: string
  icon: string
  color: string
  nodes: any[]
  edges: any[]
  tags: string[]
}

const workflowTemplates: WorkflowTemplate[] = [
  {
    id: 'content-review',
    name: '内容审核工作流',
    description: '自动审核用户提交的内容，包含敏感词检测、合规检查和人工复审环节',
    icon: '审',
    color: 'rgba(239,68,68,0.1)',
    tags: ['审核', '内容安全'],
    nodes: [
      { node_id: 'start', type: 'start', name: '开始', config: {} },
      { node_id: 'sensitive_check', type: 'agent', name: '敏感词检测', agent_name: 'content_filter', config: { prompt: '检测内容中是否包含敏感词汇' } },
      { node_id: 'compliance_check', type: 'agent', name: '合规检查', agent_name: 'compliance_checker', config: { prompt: '检查内容是否符合法规要求' } },
      { node_id: 'review_decision', type: 'condition', name: '审核判定', config: { expression: 'sensitive_check.result == "pass" and compliance_check.result == "pass"' } },
      { node_id: 'human_review', type: 'human_input', name: '人工复审', config: { prompt: '请对以下内容进行人工审核' } },
      { node_id: 'approve', type: 'end', name: '审核通过', config: {} },
      { node_id: 'reject', type: 'end', name: '审核拒绝', config: {} },
    ],
    edges: [
      { edge_id: 'e1', source_node_id: 'start', target_node_id: 'sensitive_check' },
      { edge_id: 'e2', source_node_id: 'sensitive_check', target_node_id: 'compliance_check' },
      { edge_id: 'e3', source_node_id: 'compliance_check', target_node_id: 'review_decision' },
      { edge_id: 'e4', source_node_id: 'review_decision', target_node_id: 'approve', label: '通过' },
      { edge_id: 'e5', source_node_id: 'review_decision', target_node_id: 'human_review', label: '需复审' },
      { edge_id: 'e6', source_node_id: 'review_decision', target_node_id: 'reject', label: '拒绝' },
      { edge_id: 'e7', source_node_id: 'human_review', target_node_id: 'approve', label: '通过' },
      { edge_id: 'e8', source_node_id: 'human_review', target_node_id: 'reject', label: '拒绝' },
    ],
  },
  {
    id: 'customer-service',
    name: '智能客服工作流',
    description: '自动处理客户咨询，包含意图识别、自动回复和转人工处理',
    icon: '客',
    color: 'rgba(99,102,241,0.1)',
    tags: ['客服', '自动化'],
    nodes: [
      { node_id: 'start', type: 'start', name: '接收咨询', config: {} },
      { node_id: 'intent_recognition', type: 'agent', name: '意图识别', agent_name: 'intent_classifier', config: { prompt: '识别用户意图' } },
      { node_id: 'auto_reply', type: 'agent', name: '自动回复', agent_name: 'faq_bot', config: { prompt: '根据知识库生成回复' } },
      { node_id: 'escalation_check', type: 'condition', name: '转人工判定', config: { expression: 'intent_recognition.confidence < 0.7' } },
      { node_id: 'human_agent', type: 'human_input', name: '人工客服', config: { prompt: '转接人工客服处理' } },
      { node_id: 'end', type: 'end', name: '结束', config: {} },
    ],
    edges: [
      { edge_id: 'e1', source_node_id: 'start', target_node_id: 'intent_recognition' },
      { edge_id: 'e2', source_node_id: 'intent_recognition', target_node_id: 'escalation_check' },
      { edge_id: 'e3', source_node_id: 'escalation_check', target_node_id: 'auto_reply', label: '自动回复' },
      { edge_id: 'e4', source_node_id: 'escalation_check', target_node_id: 'human_agent', label: '转人工' },
      { edge_id: 'e5', source_node_id: 'auto_reply', target_node_id: 'end' },
      { edge_id: 'e6', source_node_id: 'human_agent', target_node_id: 'end' },
    ],
  },
  {
    id: 'data-pipeline',
    name: '数据处理流水线',
    description: '自动采集、清洗、分析和汇总数据，生成结构化报告',
    icon: '数',
    color: 'rgba(5,150,105,0.1)',
    tags: ['数据', 'ETL'],
    nodes: [
      { node_id: 'start', type: 'start', name: '开始', config: {} },
      { node_id: 'collect', type: 'tool', name: '数据采集', config: { tool_name: 'data_collector' } },
      { node_id: 'clean', type: 'transform', name: '数据清洗', config: { transform_type: 'clean' } },
      { node_id: 'parallel_analysis', type: 'parallel', name: '并行分析', config: {} },
      { node_id: 'stat_analysis', type: 'agent', name: '统计分析', agent_name: 'data_analyst', config: { prompt: '进行统计分析' } },
      { node_id: 'trend_analysis', type: 'agent', name: '趋势分析', agent_name: 'trend_analyzer', config: { prompt: '分析数据趋势' } },
      { node_id: 'merge', type: 'transform', name: '结果合并', config: { transform_type: 'merge' } },
      { node_id: 'report', type: 'agent', name: '生成报告', agent_name: 'report_generator', config: { prompt: '生成分析报告' } },
      { node_id: 'end', type: 'end', name: '完成', config: {} },
    ],
    edges: [
      { edge_id: 'e1', source_node_id: 'start', target_node_id: 'collect' },
      { edge_id: 'e2', source_node_id: 'collect', target_node_id: 'clean' },
      { edge_id: 'e3', source_node_id: 'clean', target_node_id: 'parallel_analysis' },
      { edge_id: 'e4', source_node_id: 'parallel_analysis', target_node_id: 'stat_analysis', label: '分支1' },
      { edge_id: 'e5', source_node_id: 'parallel_analysis', target_node_id: 'trend_analysis', label: '分支2' },
      { edge_id: 'e6', source_node_id: 'stat_analysis', target_node_id: 'merge' },
      { edge_id: 'e7', source_node_id: 'trend_analysis', target_node_id: 'merge' },
      { edge_id: 'e8', source_node_id: 'merge', target_node_id: 'report' },
      { edge_id: 'e9', source_node_id: 'report', target_node_id: 'end' },
    ],
  },
  {
    id: 'doc-translation',
    name: '文档翻译工作流',
    description: '自动翻译文档内容，包含翻译、校对和格式还原步骤',
    icon: '译',
    color: 'rgba(168,85,247,0.1)',
    tags: ['翻译', '文档'],
    nodes: [
      { node_id: 'start', type: 'start', name: '接收文档', config: {} },
      { node_id: 'extract', type: 'tool', name: '内容提取', config: { tool_name: 'doc_extractor' } },
      { node_id: 'translate', type: 'agent', name: '翻译', agent_name: 'translator', config: { prompt: '将内容翻译为目标语言' } },
      { node_id: 'proofread', type: 'agent', name: '校对', agent_name: 'proofreader', config: { prompt: '校对翻译结果' } },
      { node_id: 'quality_check', type: 'condition', name: '质量检查', config: { expression: 'proofread.score >= 0.8' } },
      { node_id: 'retranslate', type: 'agent', name: '重新翻译', agent_name: 'translator', config: { prompt: '根据校对意见重新翻译' } },
      { node_id: 'format', type: 'transform', name: '格式还原', config: { transform_type: 'format' } },
      { node_id: 'end', type: 'end', name: '完成', config: {} },
    ],
    edges: [
      { edge_id: 'e1', source_node_id: 'start', target_node_id: 'extract' },
      { edge_id: 'e2', source_node_id: 'extract', target_node_id: 'translate' },
      { edge_id: 'e3', source_node_id: 'translate', target_node_id: 'proofread' },
      { edge_id: 'e4', source_node_id: 'proofread', target_node_id: 'quality_check' },
      { edge_id: 'e5', source_node_id: 'quality_check', target_node_id: 'format', label: '合格' },
      { edge_id: 'e6', source_node_id: 'quality_check', target_node_id: 'retranslate', label: '不合格' },
      { edge_id: 'e7', source_node_id: 'retranslate', target_node_id: 'proofread' },
      { edge_id: 'e8', source_node_id: 'format', target_node_id: 'end' },
    ],
  },
  {
    id: 'simple-approval',
    name: '简单审批流程',
    description: '标准的单级审批流程，适用于请假、报销等场景',
    icon: '批',
    color: 'rgba(234,179,8,0.1)',
    tags: ['审批', 'OA'],
    nodes: [
      { node_id: 'start', type: 'start', name: '提交申请', config: {} },
      { node_id: 'validate', type: 'agent', name: '信息校验', agent_name: 'form_validator', config: { prompt: '校验申请信息是否完整' } },
      { node_id: 'approve', type: 'human_input', name: '审批人审核', config: { prompt: '请审核此申请' } },
      { node_id: 'decision', type: 'condition', name: '审批结果', config: { expression: 'approve.action == "approve"' } },
      { node_id: 'notify_pass', type: 'tool', name: '通过通知', config: { tool_name: 'notifier' } },
      { node_id: 'notify_reject', type: 'tool', name: '驳回通知', config: { tool_name: 'notifier' } },
      { node_id: 'end', type: 'end', name: '结束', config: {} },
    ],
    edges: [
      { edge_id: 'e1', source_node_id: 'start', target_node_id: 'validate' },
      { edge_id: 'e2', source_node_id: 'validate', target_node_id: 'approve' },
      { edge_id: 'e3', source_node_id: 'approve', target_node_id: 'decision' },
      { edge_id: 'e4', source_node_id: 'decision', target_node_id: 'notify_pass', label: '通过' },
      { edge_id: 'e5', source_node_id: 'decision', target_node_id: 'notify_reject', label: '驳回' },
      { edge_id: 'e6', source_node_id: 'notify_pass', target_node_id: 'end' },
      { edge_id: 'e7', source_node_id: 'notify_reject', target_node_id: 'end' },
    ],
  },
  {
    id: 'report-generation',
    name: '周报生成工作流',
    description: '自动收集本周工作数据，汇总生成周报并发送通知',
    icon: '报',
    color: 'rgba(6,182,212,0.1)',
    tags: ['报告', '自动化'],
    nodes: [
      { node_id: 'start', type: 'start', name: '开始', config: {} },
      { node_id: 'collect_data', type: 'tool', name: '收集数据', config: { tool_name: 'data_collector' } },
      { node_id: 'summarize', type: 'agent', name: '内容摘要', agent_name: 'summarizer', config: { prompt: '总结本周工作内容' } },
      { node_id: 'generate', type: 'agent', name: '生成周报', agent_name: 'report_generator', config: { prompt: '根据摘要生成周报' } },
      { node_id: 'notify', type: 'tool', name: '发送通知', config: { tool_name: 'notifier' } },
      { node_id: 'end', type: 'end', name: '完成', config: {} },
    ],
    edges: [
      { edge_id: 'e1', source_node_id: 'start', target_node_id: 'collect_data' },
      { edge_id: 'e2', source_node_id: 'collect_data', target_node_id: 'summarize' },
      { edge_id: 'e3', source_node_id: 'summarize', target_node_id: 'generate' },
      { edge_id: 'e4', source_node_id: 'generate', target_node_id: 'notify' },
      { edge_id: 'e5', source_node_id: 'notify', target_node_id: 'end' },
    ],
  },
]

function statusLabel(status: string) {
  const map: Record<string, string> = { draft: '草稿', published: '已发布', disabled: '已禁用' }
  return map[status] || status
}

function formatTime(ts: number) {
  if (!ts) return '-'
  return new Date(ts * 1000).toLocaleString('zh-CN')
}

function openCreateDialog() {
  createForm.name = ''
  createForm.description = ''
  createForm.tagsStr = ''
  showCreateDialog.value = true
}

function selectTemplate(tpl: WorkflowTemplate) {
  selectedTemplate.value = tpl
  templateForm.name = tpl.name
  templateForm.description = tpl.description
  showTemplateConfirmDialog.value = true
}

async function createFromTemplate() {
  if (!selectedTemplate.value) return
  if (!templateForm.name.trim()) { ElMessage.warning('请输入工作流名称'); return }
  try {
    const tpl = selectedTemplate.value
    const tags = [...tpl.tags]
    await workflowApi.create({
      name: templateForm.name,
      description: templateForm.description,
      tags,
      nodes: tpl.nodes,
      edges: tpl.edges,
    } as any)
    ElMessage.success('工作流已从模板创建')
    showTemplateConfirmDialog.value = false
    showTemplateDialog.value = false
    loadWorkflows()
  } catch (e: any) {
    ElMessage.error(e.message || '创建失败')
  }
}

async function loadWorkflows() {
  loading.value = true
  try {
    const data = await workflowApi.list(statusFilter.value || undefined)
    workflows.value = data || []
  } catch {
    workflows.value = []
  } finally {
    loading.value = false
  }
}

async function createWorkflow() {
  if (!createForm.name.trim()) { ElMessage.warning('请输入工作流名称'); return }
  try {
    const tags = createForm.tagsStr.split(',').map(t => t.trim()).filter(Boolean)
    await workflowApi.create({ name: createForm.name, description: createForm.description, tags })
    ElMessage.success('创建成功')
    showCreateDialog.value = false
    createForm.name = ''; createForm.description = ''; createForm.tagsStr = ''
    loadWorkflows()
  } catch (e: any) {
    ElMessage.error(e.message || '创建失败')
  }
}

async function publishWorkflow(id: string) {
  try {
    await ElMessageBox.confirm('确定发布该工作流？发布后可执行。', '发布确认')
    await workflowApi.publish(id)
    ElMessage.success('发布成功')
    loadWorkflows()
  } catch { /* cancelled */ }
}

async function deleteWorkflow(id: string) {
  try {
    await ElMessageBox.confirm('确定删除该工作流？此操作不可恢复。', '删除确认', { type: 'warning' })
    await workflowApi.delete(id)
    ElMessage.success('删除成功')
    loadWorkflows()
  } catch { /* cancelled */ }
}

function executeWorkflow(id: string) {
  execWorkflowId.value = id
  execInput.value = '{}'
  showExecDialog.value = true
}

async function doExecute() {
  let inputData = {}
  try { inputData = JSON.parse(execInput.value) } catch { ElMessage.warning('请输入有效的 JSON'); return }
  try {
    const result = await workflowApi.execute(execWorkflowId.value, inputData)
    const statusText = result?.status === 'completed' ? '执行完成' : result?.status === 'failed' ? '执行失败' : '已提交'
    ElMessage.success(`工作流${statusText}`)
    showExecDialog.value = false
  } catch (e: any) {
    ElMessage.error(e.message || '执行失败')
  }
}

async function openDetail(wf: Workflow) {
  try {
    detailData.value = await workflowApi.get(wf.workflow_id)
    isEditingDetail.value = false
    showDetailDialog.value = true
  } catch (e: any) {
    ElMessage.error(e.message || '获取详情失败')
  }
}

function closeDetailDialog() {
  showDetailDialog.value = false
  isEditingDetail.value = false
}

function startEditDetail() {
  if (!detailData.value) return
  editForm.name = detailData.value.name
  editForm.description = detailData.value.description
  editForm.tagsStr = (detailData.value.tags || []).join(', ')
  isEditingDetail.value = true
}

function cancelEditDetail() {
  isEditingDetail.value = false
}

async function saveEditDetail() {
  if (!detailData.value) return
  if (!editForm.name.trim()) { ElMessage.warning('名称不能为空'); return }
  try {
    const tags = editForm.tagsStr.split(',').map(t => t.trim()).filter(Boolean)
    const updated = await workflowApi.update(detailData.value.workflow_id, {
      name: editForm.name,
      description: editForm.description,
      tags,
    })
    if (updated) detailData.value = updated
    isEditingDetail.value = false
    ElMessage.success('保存成功')
    loadWorkflows()
  } catch (e: any) {
    ElMessage.error(e.message || '保存失败')
  }
}

async function removeNode(nodeId: string) {
  if (!detailData.value) return
  try {
    await ElMessageBox.confirm('确定移除该节点？相关连接也会被删除。', '移除确认', { type: 'warning' })
    const updated = await workflowApi.removeNode(detailData.value.workflow_id, nodeId)
    if (updated) detailData.value = updated
    ElMessage.success('节点已移除')
  } catch { /* cancelled */ }
}

async function removeEdge(edgeId: string) {
  if (!detailData.value) return
  try {
    const updated = await workflowApi.removeEdge(detailData.value.workflow_id, edgeId)
    if (updated) detailData.value = updated
    ElMessage.success('连接已移除')
  } catch (e: any) {
    ElMessage.error(e.message || '移除失败')
  }
}

async function doAddNode() {
  if (!detailData.value) return
  if (!addNodeForm.type) { ElMessage.warning('请选择节点类型'); return }
  try {
    const node: any = { type: addNodeForm.type, name: addNodeForm.name }
    if (addNodeForm.type === 'agent') node.agent_name = addNodeForm.agent_name
    if (addNodeForm.type === 'tool') node.tool_name = addNodeForm.tool_name
    if (addNodeForm.type === 'condition') node.condition_expr = addNodeForm.condition_expr
    if (addNodeForm.type === 'delay') node.delay_seconds = addNodeForm.delay_seconds
    const updated = await workflowApi.addNode(detailData.value.workflow_id, node)
    if (updated) detailData.value = updated
    showAddNodeForm.value = false
    Object.assign(addNodeForm, { type: 'agent', name: '', agent_name: '', tool_name: '', condition_expr: '', delay_seconds: 5 })
    ElMessage.success('节点已添加')
  } catch (e: any) {
    ElMessage.error(e.message || '添加失败')
  }
}

async function doAddEdge() {
  if (!detailData.value) return
  if (!addEdgeForm.source_node_id || !addEdgeForm.target_node_id) {
    ElMessage.warning('请选择源节点和目标节点')
    return
  }
  try {
    const edge: any = {
      source_node_id: addEdgeForm.source_node_id,
      target_node_id: addEdgeForm.target_node_id,
      label: addEdgeForm.label,
    }
    const updated = await workflowApi.addEdge(detailData.value.workflow_id, edge)
    if (updated) detailData.value = updated
    showAddEdgeForm.value = false
    Object.assign(addEdgeForm, { source_node_id: '', target_node_id: '', label: '' })
    ElMessage.success('连接已添加')
  } catch (e: any) {
    ElMessage.error(e.message || '添加失败')
  }
}

onMounted(() => { loadWorkflows() })
</script>

<style scoped>
.workflow-page { max-width: 960px; }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.page-header h2 { font-size: 20px; font-weight: 700; color: var(--color-text); margin: 0; }
.header-actions { display: flex; gap: 10px; }
.btn-refresh { display: flex; align-items: center; gap: 6px; padding: 8px 14px; border-radius: var(--radius-md); color: var(--color-text-secondary); transition: all var(--transition-fast); }
.btn-refresh:hover { background: var(--color-bg); color: var(--color-text); }
.btn-outline { padding: 8px 18px; border-radius: var(--radius-md); border: 1px solid var(--color-primary); color: var(--color-primary); font-weight: 600; font-size: 13px; transition: all var(--transition-fast); }
.btn-outline:hover { background: rgba(99,102,241,0.06); }
.btn-primary { padding: 8px 18px; border-radius: var(--radius-md); background: var(--color-primary); color: white; font-weight: 600; font-size: 13px; transition: all var(--transition-fast); }
.btn-primary:hover { opacity: 0.9; }
.filter-bar { margin-bottom: 16px; }
.filter-select { padding: 8px 12px; border-radius: var(--radius-md); border: 1px solid var(--color-border-light); background: var(--color-bg-elevated); color: var(--color-text); font-size: 13px; }
.loading-state, .empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 60px 0; color: var(--color-text-tertiary); gap: 12px; }
.empty-hint { font-size: 13px; color: var(--color-text-tertiary); }
.spinner { width: 28px; height: 28px; border: 3px solid var(--color-border-light); border-top-color: var(--color-primary); border-radius: 50%; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.workflow-list { display: flex; flex-direction: column; gap: 12px; }
.workflow-card { background: var(--color-bg-elevated); border: 1px solid var(--color-border-light); border-radius: var(--radius-lg); padding: 18px; cursor: pointer; transition: all var(--transition-fast); }
.workflow-card:hover { border-color: var(--color-primary); box-shadow: 0 2px 8px rgba(99,102,241,0.08); }
.card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.wf-name { font-size: 15px; font-weight: 600; color: var(--color-text); }
.wf-status { font-size: 12px; padding: 2px 8px; border-radius: 10px; font-weight: 500; }
.wf-status.draft { background: rgba(234,179,8,0.12); color: #ca8a04; }
.wf-status.published { background: rgba(5,150,105,0.12); color: #059669; }
.wf-status.disabled { background: rgba(107,114,128,0.12); color: #6b7280; }
.wf-desc { font-size: 13px; color: var(--color-text-secondary); margin: 4px 0 10px; }
.card-meta { display: flex; gap: 16px; font-size: 12px; color: var(--color-text-tertiary); margin-bottom: 8px; }
.card-tags { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
.tag { font-size: 11px; padding: 2px 8px; border-radius: 8px; background: rgba(99,102,241,0.08); color: var(--color-primary); }
.card-actions { display: flex; gap: 8px; }
.btn-sm { padding: 4px 12px; border-radius: var(--radius-md); font-size: 12px; font-weight: 500; transition: all var(--transition-fast); }
.btn-publish { background: rgba(5,150,105,0.1); color: #059669; }
.btn-publish:hover { background: rgba(5,150,105,0.2); }
.btn-execute { background: rgba(99,102,241,0.1); color: var(--color-primary); }
.btn-execute:hover { background: rgba(99,102,241,0.2); }
.btn-danger { background: rgba(239,68,68,0.1); color: #ef4444; }
.btn-danger:hover { background: rgba(239,68,68,0.2); }
.btn-use { background: var(--color-primary); color: white; }
.btn-use:hover { opacity: 0.9; }
.template-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.template-card { display: flex; align-items: center; gap: 12px; padding: 14px; border: 1px solid var(--color-border-light); border-radius: var(--radius-lg); cursor: pointer; transition: all var(--transition-fast); }
.template-card:hover { border-color: var(--color-primary); box-shadow: 0 2px 8px rgba(99,102,241,0.08); }
.tpl-icon { width: 44px; height: 44px; border-radius: var(--radius-md); display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.tpl-icon-text { font-size: 18px; font-weight: 700; color: var(--color-text); }
.tpl-info { flex: 1; min-width: 0; }
.tpl-info h4 { font-size: 14px; font-weight: 600; color: var(--color-text); margin: 0 0 4px; }
.tpl-info p { font-size: 12px; color: var(--color-text-secondary); margin: 0 0 6px; line-height: 1.4; }
.tpl-meta { display: flex; gap: 12px; font-size: 11px; color: var(--color-text-tertiary); }
.tpl-action { flex-shrink: 0; }
.template-confirm .confirm-hint { font-size: 14px; color: var(--color-text-secondary); line-height: 1.6; }
.detail-content { max-height: 60vh; overflow-y: auto; }
.detail-toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.detail-meta { display: flex; gap: 20px; font-size: 13px; color: var(--color-text-secondary); }
.detail-actions { display: flex; gap: 8px; }
.detail-desc { font-size: 13px; color: var(--color-text-secondary); margin-bottom: 12px; }
.detail-tags { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; }
.section-header { display: flex; justify-content: space-between; align-items: center; }
.section-header h4 { font-size: 14px; font-weight: 600; color: var(--color-text); margin: 16px 0 8px; }
.btn-add { background: rgba(5,150,105,0.1); color: #059669; }
.btn-add:hover { background: rgba(5,150,105,0.2); }
.btn-save { background: rgba(5,150,105,0.1); color: #059669; }
.btn-save:hover { background: rgba(5,150,105,0.2); }
.btn-cancel { background: rgba(107,114,128,0.1); color: #6b7280; }
.btn-cancel:hover { background: rgba(107,114,128,0.2); }
.btn-remove { background: rgba(239,68,68,0.08); color: #ef4444; font-size: 11px; padding: 1px 6px; }
.btn-remove:hover { background: rgba(239,68,68,0.15); }
.edit-form { display: flex; flex-direction: column; gap: 12px; margin-bottom: 12px; }
.edit-row { display: flex; align-items: flex-start; gap: 12px; }
.edit-row label { width: 50px; flex-shrink: 0; font-size: 13px; color: var(--color-text-secondary); line-height: 32px; text-align: right; }
.edit-row .el-input, .edit-row .el-textarea { flex: 1; }
.empty-hint { font-size: 13px; color: var(--color-text-tertiary); padding: 8px 0; }
.node-list, .edge-list { display: flex; flex-direction: column; gap: 6px; }
.node-item { display: flex; align-items: center; gap: 8px; padding: 6px 10px; background: var(--color-bg); border-radius: var(--radius-md); font-size: 13px; }
.node-type-badge { font-size: 11px; padding: 1px 6px; border-radius: 6px; font-weight: 500; }
.node-type-badge.agent { background: rgba(99,102,241,0.12); color: var(--color-primary); }
.node-type-badge.condition { background: rgba(234,179,8,0.12); color: #ca8a04; }
.node-type-badge.tool { background: rgba(5,150,105,0.12); color: #059669; }
.node-type-badge.start, .node-type-badge.end { background: rgba(107,114,128,0.12); color: #6b7280; }
.node-type-badge.parallel { background: rgba(168,85,247,0.12); color: #a855f7; }
.node-type-badge.transform { background: rgba(6,182,212,0.12); color: #06b6d4; }
.node-type-badge.human_input { background: rgba(249,115,22,0.12); color: #f97316; }
.node-type-badge.delay { background: rgba(107,114,128,0.12); color: #6b7280; }
.node-name { color: var(--color-text); }
.node-agent { font-size: 11px; color: var(--color-text-tertiary); }
.edge-item { display: flex; align-items: center; gap: 6px; padding: 4px 10px; font-size: 13px; color: var(--color-text-secondary); }
.edge-node { padding: 2px 6px; background: var(--color-bg); border-radius: 4px; font-family: monospace; font-size: 12px; }
.edge-arrow { color: var(--color-text-tertiary); }
.edge-label { font-size: 11px; color: var(--color-primary); }
.btn-guide { padding: 8px 18px; border-radius: var(--radius-md); border: 1px solid var(--color-primary); color: var(--color-primary); font-weight: 600; font-size: 13px; transition: all var(--transition-fast); }
.btn-guide:hover { background: rgba(99,102,241,0.06); }
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

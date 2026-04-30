<template>
  <div class="approval-page">
    <div class="page-header">
      <h2>审批管理</h2>
      <div class="header-actions">
        <button class="btn-refresh" @click="loadPending">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path fill-rule="evenodd" d="M8 3a5 5 0 110 10A5 5 0 018 3zm0 1.5a3.5 3.5 0 100 7 3.5 3.5 0 000-7z" opacity="0.4" /><path d="M8 1a7 7 0 016.95 6.25h-1.5A5.5 5.5 0 008 2.5a5.5 5.5 0 00-5.45 4.75h-1.5A7 7 0 018 1z" /></svg>
          刷新
        </button>
        <button class="btn-primary" @click="showCreateDialog = true">创建审批</button>
      </div>
    </div>

    <div class="guide-banner" v-if="!guideDismissed">
      <div class="guide-banner-content">
        <span class="guide-icon">?</span>
        <span>审批管理用于对敏感操作进行人工审核。Agent 在执行高风险操作前，会自动创建审批请求，等待审批人确认后才继续执行。</span>
        <button class="guide-link" @click="showGuideDialog = true">查看详细指南</button>
        <button class="guide-close" @click="guideDismissed = true">&times;</button>
      </div>
    </div>

    <div class="filter-bar">
      <select v-model="statusFilter" class="filter-select" @change="loadPending">
        <option value="">全部状态</option>
        <option value="pending">待审批</option>
        <option value="approved">已通过</option>
        <option value="rejected">已拒绝</option>
        <option value="expired">已过期</option>
        <option value="cancelled">已取消</option>
      </select>
    </div>

    <div v-if="loading" class="loading-state"><div class="spinner" /><span>加载中...</span></div>
    <div v-else-if="approvals.length === 0" class="empty-state">
      <p>暂无审批记录</p>
      <p class="empty-hint">当 Agent 需要执行敏感操作时，会自动创建审批请求</p>
    </div>

    <div v-else class="approval-list">
      <div v-for="item in approvals" :key="item.approval_id" class="approval-card">
        <div class="card-header">
          <span class="approval-id">#{{ item.approval_id.slice(0, 8) }}</span>
          <span class="approval-status" :class="item.status">{{ statusLabel(item.status) }}</span>
        </div>
        <div class="card-body">
          <div class="info-row"><span class="label">操作工具</span><span class="value">{{ item.tool_name }}</span></div>
          <div class="info-row"><span class="label">发起人</span><span class="value">{{ item.user_id || '-' }}</span></div>
          <div class="info-row"><span class="label">Agent</span><span class="value">{{ item.agent_name || '-' }}</span></div>
          <div v-if="item.reason" class="info-row"><span class="label">原因</span><span class="value">{{ item.reason }}</span></div>
          <div class="info-row"><span class="label">审批进度</span><span class="value">{{ item.current_step }} / {{ item.total_steps }}</span></div>
          <div class="info-row"><span class="label">审批人</span><span class="value">{{ item.approver || '-' }}</span></div>
          <div class="info-row"><span class="label">创建时间</span><span class="value">{{ formatTime(item.created_at) }}</span></div>
          <div v-if="item.expires_at" class="info-row"><span class="label">过期时间</span><span class="value">{{ formatTime(item.expires_at) }}</span></div>
        </div>
        <div v-if="item.status === 'pending'" class="card-actions">
          <button class="btn-sm btn-approve" @click="handleApprove(item)">通过</button>
          <button class="btn-sm btn-reject" @click="handleReject(item)">拒绝</button>
          <button class="btn-sm btn-cancel" @click="handleCancel(item)">取消</button>
        </div>
      </div>
    </div>

    <el-dialog v-model="showGuideDialog" title="审批管理使用指南" width="640px">
      <div class="guide-content">
        <div class="guide-section">
          <h4>什么是审批管理？</h4>
          <p>审批管理是平台的安全机制，用于对 Agent 执行的敏感操作进行人工审核。当 Agent 需要执行高风险操作（如删除数据、发送邮件、访问外部系统等）时，系统会自动暂停执行并创建审批请求，等待审批人确认后才继续。</p>
        </div>

        <div class="guide-section">
          <h4>使用流程</h4>
          <div class="flow-steps">
            <div class="flow-step">
              <div class="step-num">1</div>
              <div class="step-content">
                <strong>Agent 触发审批</strong>
                <p>Agent 在执行标记为需要审批的工具时，自动创建审批请求</p>
              </div>
            </div>
            <div class="flow-step">
              <div class="step-num">2</div>
              <div class="step-content">
                <strong>审批人收到通知</strong>
                <p>审批请求出现在审批管理页面，状态为"待审批"</p>
              </div>
            </div>
            <div class="flow-step">
              <div class="step-num">3</div>
              <div class="step-content">
                <strong>审批人操作</strong>
                <p>审批人查看请求详情后，选择"通过"或"拒绝"，并填写审批人名称和备注</p>
              </div>
            </div>
            <div class="flow-step">
              <div class="step-num">4</div>
              <div class="step-content">
                <strong>Agent 继续或终止</strong>
                <p>审批通过后 Agent 继续执行操作；拒绝则 Agent 终止该操作</p>
              </div>
            </div>
          </div>
        </div>

        <div class="guide-section">
          <h4>手动创建审批示例</h4>
          <p>你也可以手动创建审批请求，典型场景如下：</p>
          <div class="example-cards">
            <div class="example-card">
              <div class="example-title">场景1: 数据删除审批</div>
              <div class="example-detail">
                <div>工具名称: <code>database_delete</code></div>
                <div>审批原因: <code>需要删除过期的用户数据</code></div>
                <div>Agent: <code>data_manager</code></div>
                <div>超时: <code>24 小时</code></div>
              </div>
            </div>
            <div class="example-card">
              <div class="example-title">场景2: 邮件发送审批</div>
              <div class="example-detail">
                <div>工具名称: <code>email_sender</code></div>
                <div>审批原因: <code>向客户发送重要通知邮件</code></div>
                <div>Agent: <code>notification_agent</code></div>
                <div>超时: <code>4 小时</code></div>
              </div>
            </div>
            <div class="example-card">
              <div class="example-title">场景3: 外部API调用审批</div>
              <div class="example-detail">
                <div>工具名称: <code>external_api</code></div>
                <div>审批原因: <code>调用第三方支付接口</code></div>
                <div>Agent: <code>payment_agent</code></div>
                <div>超时: <code>2 小时</code></div>
              </div>
            </div>
          </div>
        </div>

        <div class="guide-section">
          <h4>状态说明</h4>
          <div class="status-list">
            <div class="status-item"><span class="status-badge pending">待审批</span> 等待审批人审核</div>
            <div class="status-item"><span class="status-badge approved">已通过</span> 审批人已同意，Agent 可继续执行</div>
            <div class="status-item"><span class="status-badge rejected">已拒绝</span> 审批人已拒绝，Agent 终止操作</div>
            <div class="status-item"><span class="status-badge expired">已过期</span> 超过超时时间未审批，自动失效</div>
            <div class="status-item"><span class="status-badge cancelled">已取消</span> 申请人主动取消审批</div>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button type="primary" @click="showGuideDialog = false">知道了</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showCreateDialog" title="创建审批" width="520px" :close-on-click-modal="false">
      <el-form label-width="90px">
        <el-form-item label="工具名称" required><el-input v-model="createForm.tool_name" placeholder="敏感操作工具名称，如 database_delete" /></el-form-item>
        <el-form-item label="审批原因"><el-input v-model="createForm.reason" type="textarea" :rows="2" placeholder="请输入审批原因，如：需要删除过期的用户数据" /></el-form-item>
        <el-form-item label="Agent"><el-input v-model="createForm.agent_name" placeholder="关联的 Agent 名称，如 data_manager" /></el-form-item>
        <el-form-item label="超时(小时)"><el-input-number v-model="createForm.timeout_hours" :min="1" :max="168" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" @click="createApproval">提交</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showActionDialog" :title="actionTitle" width="400px" :close-on-click-modal="false">
      <el-form label-width="70px">
        <el-form-item label="审批人" required><el-input v-model="actionForm.approver" placeholder="请输入审批人" /></el-form-item>
        <el-form-item label="备注"><el-input v-model="actionForm.comment" type="textarea" :rows="2" placeholder="审批备注" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showActionDialog = false">取消</el-button>
        <el-button type="primary" @click="doAction">确认</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { approvalApi, type ApprovalItem } from '../../api/approval'

const loading = ref(false)
const approvals = ref<ApprovalItem[]>([])
const statusFilter = ref('')
const guideDismissed = ref(false)

const showGuideDialog = ref(false)
const showCreateDialog = ref(false)
const createForm = reactive({ tool_name: '', reason: '', agent_name: '', timeout_hours: 24 })

const showActionDialog = ref(false)
const actionType = ref<'approve' | 'reject' | 'cancel'>('approve')
const actionTarget = ref<ApprovalItem | null>(null)
const actionForm = reactive({ approver: '', comment: '' })

const actionTitle = ref('')

function statusLabel(status: string) {
  const map: Record<string, string> = { pending: '待审批', approved: '已通过', rejected: '已拒绝', expired: '已过期', cancelled: '已取消' }
  return map[status] || status
}

function formatTime(ts: number) {
  if (!ts) return '-'
  return new Date(ts * 1000).toLocaleString('zh-CN')
}

async function loadPending() {
  loading.value = true
  try {
    const data = await approvalApi.listPending({ status: statusFilter.value || undefined })
    approvals.value = data?.items || []
  } catch {
    approvals.value = []
  } finally {
    loading.value = false
  }
}

async function createApproval() {
  if (!createForm.tool_name.trim()) { ElMessage.warning('请输入工具名称'); return }
  try {
    await approvalApi.create({ tool_name: createForm.tool_name, reason: createForm.reason, agent_name: createForm.agent_name, timeout_hours: createForm.timeout_hours })
    ElMessage.success('审批创建成功')
    showCreateDialog.value = false
    createForm.tool_name = ''; createForm.reason = ''; createForm.agent_name = ''; createForm.timeout_hours = 24
    loadPending()
  } catch (e: any) {
    ElMessage.error(e.message || '创建失败')
  }
}

function handleApprove(item: ApprovalItem) {
  actionType.value = 'approve'
  actionTarget.value = item
  actionTitle.value = '通过审批'
  actionForm.approver = ''; actionForm.comment = ''
  showActionDialog.value = true
}

function handleReject(item: ApprovalItem) {
  actionType.value = 'reject'
  actionTarget.value = item
  actionTitle.value = '拒绝审批'
  actionForm.approver = ''; actionForm.comment = ''
  showActionDialog.value = true
}

function handleCancel(item: ApprovalItem) {
  actionType.value = 'cancel'
  actionTarget.value = item
  actionTitle.value = '取消审批'
  actionForm.approver = ''; actionForm.comment = ''
  showActionDialog.value = true
}

async function doAction() {
  if (!actionForm.approver.trim()) { ElMessage.warning('请输入审批人'); return }
  if (!actionTarget.value) return
  const id = actionTarget.value.approval_id
  try {
    if (actionType.value === 'approve') {
      await approvalApi.approve(id, actionForm.approver, actionForm.comment)
      ElMessage.success('审批通过')
    } else if (actionType.value === 'reject') {
      await approvalApi.reject(id, actionForm.approver, actionForm.comment)
      ElMessage.success('已拒绝')
    } else {
      await approvalApi.cancel(id, actionForm.approver, actionForm.comment)
      ElMessage.success('已取消')
    }
    showActionDialog.value = false
    loadPending()
  } catch (e: any) {
    ElMessage.error(e.message || '操作失败')
  }
}

onMounted(() => { loadPending() })
</script>

<style scoped>
.approval-page { max-width: 960px; }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.page-header h2 { font-size: 20px; font-weight: 700; color: var(--color-text); margin: 0; }
.header-actions { display: flex; gap: 10px; }
.btn-refresh { display: flex; align-items: center; gap: 6px; padding: 8px 14px; border-radius: var(--radius-md); color: var(--color-text-secondary); transition: all var(--transition-fast); }
.btn-refresh:hover { background: var(--color-bg); color: var(--color-text); }
.btn-outline { padding: 8px 18px; border-radius: var(--radius-md); border: 1px solid var(--color-primary); color: var(--color-primary); font-weight: 600; font-size: 13px; transition: all var(--transition-fast); }
.btn-outline:hover { background: rgba(99,102,241,0.06); }
.btn-primary { padding: 8px 18px; border-radius: var(--radius-md); background: var(--color-primary); color: white; font-weight: 600; font-size: 13px; transition: all var(--transition-fast); }
.btn-primary:hover { opacity: 0.9; }
.guide-banner { background: rgba(99,102,241,0.06); border: 1px solid rgba(99,102,241,0.15); border-radius: var(--radius-lg); padding: 12px 16px; margin-bottom: 16px; }
.guide-banner-content { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--color-text-secondary); line-height: 1.5; }
.guide-icon { width: 22px; height: 22px; border-radius: 50%; background: var(--color-primary); color: white; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; flex-shrink: 0; }
.guide-link { color: var(--color-primary); font-weight: 500; cursor: pointer; white-space: nowrap; }
.guide-link:hover { text-decoration: underline; }
.guide-close { margin-left: auto; color: var(--color-text-tertiary); font-size: 18px; cursor: pointer; padding: 0 4px; line-height: 1; }
.guide-close:hover { color: var(--color-text); }
.filter-bar { margin-bottom: 16px; }
.filter-select { padding: 8px 12px; border-radius: var(--radius-md); border: 1px solid var(--color-border-light); background: var(--color-bg-elevated); color: var(--color-text); font-size: 13px; }
.loading-state, .empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 60px 0; color: var(--color-text-tertiary); gap: 12px; }
.empty-hint { font-size: 13px; color: var(--color-text-tertiary); }
.spinner { width: 28px; height: 28px; border: 3px solid var(--color-border-light); border-top-color: var(--color-primary); border-radius: 50%; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.approval-list { display: flex; flex-direction: column; gap: 12px; }
.approval-card { background: var(--color-bg-elevated); border: 1px solid var(--color-border-light); border-radius: var(--radius-lg); padding: 18px; transition: all var(--transition-fast); }
.approval-card:hover { border-color: var(--color-primary); box-shadow: 0 2px 8px rgba(99,102,241,0.08); }
.card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.approval-id { font-size: 14px; font-weight: 600; color: var(--color-text); font-family: monospace; }
.approval-status { font-size: 12px; padding: 2px 8px; border-radius: 10px; font-weight: 500; }
.approval-status.pending { background: rgba(234,179,8,0.12); color: #ca8a04; }
.approval-status.approved { background: rgba(5,150,105,0.12); color: #059669; }
.approval-status.rejected { background: rgba(239,68,68,0.12); color: #ef4444; }
.approval-status.expired { background: rgba(107,114,128,0.12); color: #6b7280; }
.approval-status.cancelled { background: rgba(107,114,128,0.12); color: #6b7280; }
.card-body { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 24px; }
.info-row { display: flex; gap: 8px; font-size: 13px; }
.info-row .label { color: var(--color-text-tertiary); min-width: 70px; }
.info-row .value { color: var(--color-text); }
.card-actions { display: flex; gap: 8px; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--color-border-light); }
.btn-sm { padding: 4px 12px; border-radius: var(--radius-md); font-size: 12px; font-weight: 500; transition: all var(--transition-fast); }
.btn-approve { background: rgba(5,150,105,0.1); color: #059669; }
.btn-approve:hover { background: rgba(5,150,105,0.2); }
.btn-reject { background: rgba(239,68,68,0.1); color: #ef4444; }
.btn-reject:hover { background: rgba(239,68,68,0.2); }
.btn-cancel { background: rgba(107,114,128,0.1); color: #6b7280; }
.btn-cancel:hover { background: rgba(107,114,128,0.2); }
.guide-content { max-height: 60vh; overflow-y: auto; }
.guide-section { margin-bottom: 20px; }
.guide-section h4 { font-size: 15px; font-weight: 600; color: var(--color-text); margin: 0 0 8px; }
.guide-section p { font-size: 13px; color: var(--color-text-secondary); line-height: 1.6; margin: 0 0 8px; }
.flow-steps { display: flex; flex-direction: column; gap: 12px; margin-top: 8px; }
.flow-step { display: flex; gap: 12px; align-items: flex-start; }
.step-num { width: 28px; height: 28px; border-radius: 50%; background: var(--color-primary); color: white; display: flex; align-items: center; justify-content: center; font-size: 13px; font-weight: 700; flex-shrink: 0; }
.step-content strong { font-size: 13px; color: var(--color-text); display: block; margin-bottom: 2px; }
.step-content p { font-size: 12px; color: var(--color-text-tertiary); margin: 0; }
.example-cards { display: flex; flex-direction: column; gap: 10px; margin-top: 8px; }
.example-card { background: var(--color-bg); border-radius: var(--radius-md); padding: 12px; }
.example-title { font-size: 13px; font-weight: 600; color: var(--color-text); margin-bottom: 6px; }
.example-detail { font-size: 12px; color: var(--color-text-secondary); line-height: 1.8; }
.example-detail code { background: rgba(99,102,241,0.08); color: var(--color-primary); padding: 1px 6px; border-radius: 4px; font-size: 12px; }
.status-list { display: flex; flex-direction: column; gap: 8px; }
.status-item { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--color-text-secondary); }
.status-badge { font-size: 11px; padding: 2px 8px; border-radius: 10px; font-weight: 500; min-width: 56px; text-align: center; }
.status-badge.pending { background: rgba(234,179,8,0.12); color: #ca8a04; }
.status-badge.approved { background: rgba(5,150,105,0.12); color: #059669; }
.status-badge.rejected { background: rgba(239,68,68,0.12); color: #ef4444; }
.status-badge.expired { background: rgba(107,114,128,0.12); color: #6b7280; }
.status-badge.cancelled { background: rgba(107,114,128,0.12); color: #6b7280; }
</style>

<template>
  <div class="scheduler-page">
    <div class="page-header">
      <h2>定时任务</h2>
      <div class="header-actions">
        <button class="btn-refresh" @click="loadTasks">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path fill-rule="evenodd" d="M8 3a5 5 0 110 10A5 5 0 018 3zm0 1.5a3.5 3.5 0 100 7 3.5 3.5 0 000-7z" opacity="0.4" /><path d="M8 1a7 7 0 016.95 6.25h-1.5A5.5 5.5 0 008 2.5a5.5 5.5 0 00-5.45 4.75h-1.5A7 7 0 018 1z" /></svg>
          刷新
        </button>
        <button class="btn-primary" @click="showCreateDialog = true">创建任务</button>
      </div>
    </div>

    <div class="guide-banner" v-if="!guideDismissed">
      <div class="guide-banner-content">
        <span class="guide-icon">?</span>
        <span>定时任务可以按计划自动执行 Agent，支持 Cron 表达式和固定间隔两种触发方式。适用于定期报告、数据同步等场景。</span>
        <button class="guide-link" @click="showGuideDialog = true">查看详细指南</button>
        <button class="guide-close" @click="guideDismissed = true">&times;</button>
      </div>
    </div>

    <div v-if="loading" class="loading-state"><div class="spinner" /><span>加载中...</span></div>
    <div v-else-if="tasks.length === 0" class="empty-state">
      <p>暂无定时任务</p>
      <p class="empty-hint">创建定时任务，让 Agent 按计划自动执行</p>
    </div>

    <div v-else class="task-list">
      <div v-for="task in tasks" :key="task.task_id" class="task-card">
        <div class="card-header">
          <div class="title-row">
            <span class="task-name">{{ task.name }}</span>
            <label class="toggle-switch">
              <input type="checkbox" :checked="task.enabled" @change="toggleTask(task)" />
              <span class="toggle-slider" />
            </label>
          </div>
          <span class="task-trigger-badge" :class="task.trigger_type">{{ triggerLabel(task.trigger_type) }}: {{ task.trigger_value }}</span>
        </div>
        <div class="card-body">
          <div class="info-row"><span class="label">Agent</span><span class="value">{{ task.agent_name || '-' }}</span></div>
          <div class="info-row"><span class="label">任务描述</span><span class="value">{{ task.task_prompt || '-' }}</span></div>
          <div class="info-row"><span class="label">推送渠道</span><span class="value">{{ task.channel }}</span></div>
          <div class="info-row"><span class="label">目标用户</span><span class="value">{{ task.target_user || '-' }}</span></div>
          <div class="info-row"><span class="label">上次执行</span><span class="value">{{ task.last_run_at ? formatTime(task.last_run_at) : '未执行' }}</span></div>
          <div class="info-row"><span class="label">下次执行</span><span class="value">{{ task.next_run_at ? formatTime(task.next_run_at) : '-' }}</span></div>
        </div>
        <div class="card-actions">
          <button class="btn-sm btn-edit" @click="openEdit(task)">编辑</button>
          <button class="btn-sm btn-danger" @click="deleteTask(task.task_id)">删除</button>
        </div>
      </div>
    </div>

    <el-dialog v-model="showGuideDialog" title="定时任务使用指南" width="640px">
      <div class="guide-content">
        <div class="guide-section">
          <h4>什么是定时任务？</h4>
          <p>定时任务允许你按计划自动触发 Agent 执行特定操作。你可以使用 Cron 表达式设定精确的执行时间，或使用固定间隔让 Agent 周期性运行。</p>
        </div>

        <div class="guide-section">
          <h4>使用流程</h4>
          <div class="flow-steps">
            <div class="flow-step">
              <div class="step-num">1</div>
              <div class="step-content">
                <strong>创建任务</strong>
                <p>填写任务名称、选择触发类型和触发值、指定要执行的 Agent</p>
              </div>
            </div>
            <div class="flow-step">
              <div class="step-num">2</div>
              <div class="step-content">
                <strong>启用任务</strong>
                <p>创建后任务默认启用，你可以通过开关随时启用/禁用</p>
              </div>
            </div>
            <div class="flow-step">
              <div class="step-num">3</div>
              <div class="step-content">
                <strong>自动执行</strong>
                <p>到达触发时间后，系统自动调用指定 Agent 执行任务描述中的操作</p>
              </div>
            </div>
            <div class="flow-step">
              <div class="step-num">4</div>
              <div class="step-content">
                <strong>结果推送</strong>
                <p>执行结果通过指定渠道推送给目标用户</p>
              </div>
            </div>
          </div>
        </div>

        <div class="guide-section">
          <h4>Cron 表达式说明</h4>
          <p>Cron 表达式格式: <code>分 时 日 月 周</code></p>
          <div class="cron-table">
            <div class="cron-row cron-header">
              <span>位置</span><span>含义</span><span>取值范围</span><span>示例</span>
            </div>
            <div class="cron-row"><span>第1位</span><span>分钟</span><span>0-59</span><span>0, 30</span></div>
            <div class="cron-row"><span>第2位</span><span>小时</span><span>0-23</span><span>9, 14</span></div>
            <div class="cron-row"><span>第3位</span><span>日期</span><span>1-31</span><span>1, 15</span></div>
            <div class="cron-row"><span>第4位</span><span>月份</span><span>1-12</span><span>1, 6</span></div>
            <div class="cron-row"><span>第5位</span><span>星期</span><span>0-6(0=周日)</span><span>1-5</span></div>
          </div>
          <p style="margin-top: 8px;">特殊符号: <code>*</code> 任意值, <code>,</code> 列举, <code>-</code> 范围, <code>/</code> 步长</p>
        </div>

        <div class="guide-section">
          <h4>常用示例</h4>
          <div class="example-cards">
            <div class="example-card">
              <div class="example-title">每日早报</div>
              <div class="example-detail">
                <div>触发类型: <code>Cron</code></div>
                <div>触发值: <code>0 9 * * *</code> (每天 9:00)</div>
                <div>Agent: <code>news_agent</code></div>
                <div>任务描述: <code>汇总今日重要新闻，生成早报摘要</code></div>
              </div>
            </div>
            <div class="example-card">
              <div class="example-title">工作日周报提醒</div>
              <div class="example-detail">
                <div>触发类型: <code>Cron</code></div>
                <div>触发值: <code>0 17 * * 5</code> (每周五 17:00)</div>
                <div>Agent: <code>reminder_agent</code></div>
                <div>任务描述: <code>提醒团队成员提交本周工作周报</code></div>
              </div>
            </div>
            <div class="example-card">
              <div class="example-title">系统健康检查</div>
              <div class="example-detail">
                <div>触发类型: <code>间隔</code></div>
                <div>触发值: <code>300</code> (每 5 分钟)</div>
                <div>Agent: <code>monitor_agent</code></div>
                <div>任务描述: <code>检查系统各服务运行状态，异常时告警</code></div>
              </div>
            </div>
            <div class="example-card">
              <div class="example-title">数据备份</div>
              <div class="example-detail">
                <div>触发类型: <code>Cron</code></div>
                <div>触发值: <code>0 2 * * *</code> (每天凌晨 2:00)</div>
                <div>Agent: <code>backup_agent</code></div>
                <div>任务描述: <code>执行数据库备份，验证备份完整性</code></div>
              </div>
            </div>
            <div class="example-card">
              <div class="example-title">月度数据统计</div>
              <div class="example-detail">
                <div>触发类型: <code>Cron</code></div>
                <div>触发值: <code>0 10 1 * *</code> (每月1号 10:00)</div>
                <div>Agent: <code>data_analyst</code></div>
                <div>任务描述: <code>汇总上月业务数据，生成月度统计报告</code></div>
              </div>
            </div>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button type="primary" @click="showGuideDialog = false">知道了</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showCreateDialog" title="创建定时任务" width="520px" :close-on-click-modal="false">
      <el-form label-width="90px">
        <el-form-item label="任务名称" required><el-input v-model="createForm.name" placeholder="如: 每日早报" /></el-form-item>
        <el-form-item label="触发类型" required>
          <el-select v-model="createForm.trigger_type" style="width: 100%">
            <el-option label="Cron 表达式" value="cron" />
            <el-option label="固定间隔" value="interval" />
          </el-select>
        </el-form-item>
        <el-form-item label="触发值" required>
          <el-input v-model="createForm.trigger_value" :placeholder="createForm.trigger_type === 'cron' ? 'Cron 表达式，如: 0 9 * * 1-5' : '间隔秒数，如: 3600'" />
        </el-form-item>
        <el-form-item label="Agent"><el-input v-model="createForm.agent_name" placeholder="执行的 Agent 名称，如 news_agent" /></el-form-item>
        <el-form-item label="任务描述"><el-input v-model="createForm.task_prompt" type="textarea" :rows="2" placeholder="任务执行时的提示词，如：汇总今日重要新闻" /></el-form-item>
        <el-form-item label="推送渠道"><el-input v-model="createForm.channel" placeholder="默认: web" /></el-form-item>
        <el-form-item label="目标用户"><el-input v-model="createForm.target_user" placeholder="推送目标用户" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" @click="createTask">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showEditDialog" title="编辑定时任务" width="520px" :close-on-click-modal="false">
      <el-form label-width="90px">
        <el-form-item label="任务名称"><el-input v-model="editForm.name" /></el-form-item>
        <el-form-item label="触发类型">
          <el-select v-model="editForm.trigger_type" style="width: 100%">
            <el-option label="Cron 表达式" value="cron" />
            <el-option label="固定间隔" value="interval" />
          </el-select>
        </el-form-item>
        <el-form-item label="触发值"><el-input v-model="editForm.trigger_value" /></el-form-item>
        <el-form-item label="Agent"><el-input v-model="editForm.agent_name" /></el-form-item>
        <el-form-item label="任务描述"><el-input v-model="editForm.task_prompt" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="推送渠道"><el-input v-model="editForm.channel" /></el-form-item>
        <el-form-item label="目标用户"><el-input v-model="editForm.target_user" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showEditDialog = false">取消</el-button>
        <el-button type="primary" @click="updateTask">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { schedulerApi, type ScheduledTask } from '../../api/scheduler'

const loading = ref(false)
const tasks = ref<ScheduledTask[]>([])
const guideDismissed = ref(false)

const showGuideDialog = ref(false)
const showCreateDialog = ref(false)
const createForm = reactive({ name: '', trigger_type: 'cron', trigger_value: '', agent_name: '', task_prompt: '', channel: 'web', target_user: '' })

const showEditDialog = ref(false)
const editForm = reactive({ task_id: '', name: '', trigger_type: '', trigger_value: '', agent_name: '', task_prompt: '', channel: '', target_user: '' })

function triggerLabel(type: string) { return type === 'cron' ? 'Cron' : '间隔' }
function formatTime(ts: number) { if (!ts) return '-'; return new Date(ts * 1000).toLocaleString('zh-CN') }

async function loadTasks() {
  loading.value = true
  try {
    const data = await schedulerApi.list()
    tasks.value = data?.items || []
  } catch { tasks.value = [] }
  finally { loading.value = false }
}

async function createTask() {
  if (!createForm.name.trim()) { ElMessage.warning('请输入任务名称'); return }
  if (!createForm.trigger_value.trim()) { ElMessage.warning('请输入触发值'); return }
  try {
    await schedulerApi.create(createForm)
    ElMessage.success('创建成功')
    showCreateDialog.value = false
    Object.assign(createForm, { name: '', trigger_type: 'cron', trigger_value: '', agent_name: '', task_prompt: '', channel: 'web', target_user: '' })
    loadTasks()
  } catch (e: any) { ElMessage.error(e.message || '创建失败') }
}

function openEdit(task: ScheduledTask) {
  Object.assign(editForm, { task_id: task.task_id, name: task.name, trigger_type: task.trigger_type, trigger_value: task.trigger_value, agent_name: task.agent_name, task_prompt: task.task_prompt, channel: task.channel, target_user: task.target_user })
  showEditDialog.value = true
}

async function updateTask() {
  try {
    const { task_id, ...data } = editForm
    await schedulerApi.update(task_id, data)
    ElMessage.success('更新成功')
    showEditDialog.value = false
    loadTasks()
  } catch (e: any) { ElMessage.error(e.message || '更新失败') }
}

async function toggleTask(task: ScheduledTask) {
  try {
    await schedulerApi.toggle(task.task_id)
    loadTasks()
  } catch (e: any) { ElMessage.error(e.message || '操作失败') }
}

async function deleteTask(id: string) {
  try {
    await ElMessageBox.confirm('确定删除该定时任务？', '删除确认', { type: 'warning' })
    await schedulerApi.delete(id)
    ElMessage.success('删除成功')
    loadTasks()
  } catch { /* cancelled */ }
}

onMounted(() => { loadTasks() })
</script>

<style scoped>
.scheduler-page { max-width: 960px; }
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
.loading-state, .empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 60px 0; color: var(--color-text-tertiary); gap: 12px; }
.empty-hint { font-size: 13px; color: var(--color-text-tertiary); }
.spinner { width: 28px; height: 28px; border: 3px solid var(--color-border-light); border-top-color: var(--color-primary); border-radius: 50%; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.task-list { display: flex; flex-direction: column; gap: 12px; }
.task-card { background: var(--color-bg-elevated); border: 1px solid var(--color-border-light); border-radius: var(--radius-lg); padding: 18px; transition: all var(--transition-fast); }
.task-card:hover { border-color: var(--color-primary); box-shadow: 0 2px 8px rgba(99,102,241,0.08); }
.card-header { margin-bottom: 10px; }
.title-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
.task-name { font-size: 15px; font-weight: 600; color: var(--color-text); }
.toggle-switch { position: relative; width: 36px; height: 20px; display: inline-block; }
.toggle-switch input { opacity: 0; width: 0; height: 0; }
.toggle-slider { position: absolute; cursor: pointer; inset: 0; background: var(--color-border); border-radius: 20px; transition: var(--transition-fast); }
.toggle-slider::before { content: ''; position: absolute; width: 16px; height: 16px; left: 2px; bottom: 2px; background: white; border-radius: 50%; transition: var(--transition-fast); }
.toggle-switch input:checked + .toggle-slider { background: var(--color-primary); }
.toggle-switch input:checked + .toggle-slider::before { transform: translateX(16px); }
.task-trigger-badge { font-size: 12px; padding: 2px 8px; border-radius: 10px; font-weight: 500; }
.task-trigger-badge.cron { background: rgba(99,102,241,0.1); color: var(--color-primary); }
.task-trigger-badge.interval { background: rgba(6,182,212,0.1); color: #06b6d4; }
.card-body { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 24px; }
.info-row { display: flex; gap: 8px; font-size: 13px; }
.info-row .label { color: var(--color-text-tertiary); min-width: 70px; }
.info-row .value { color: var(--color-text); word-break: break-all; }
.card-actions { display: flex; gap: 8px; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--color-border-light); }
.btn-sm { padding: 4px 12px; border-radius: var(--radius-md); font-size: 12px; font-weight: 500; transition: all var(--transition-fast); }
.btn-edit { background: rgba(99,102,241,0.1); color: var(--color-primary); }
.btn-edit:hover { background: rgba(99,102,241,0.2); }
.btn-danger { background: rgba(239,68,68,0.1); color: #ef4444; }
.btn-danger:hover { background: rgba(239,68,68,0.2); }
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
.cron-table { display: flex; flex-direction: column; gap: 0; border: 1px solid var(--color-border-light); border-radius: var(--radius-md); overflow: hidden; margin-top: 8px; }
.cron-row { display: grid; grid-template-columns: 60px 60px 100px 1fr; font-size: 12px; padding: 6px 12px; border-bottom: 1px solid var(--color-border-light); }
.cron-row:last-child { border-bottom: none; }
.cron-header { background: var(--color-bg); font-weight: 600; color: var(--color-text); }
.cron-row span { color: var(--color-text-secondary); }
.cron-header span { color: var(--color-text); }
.example-cards { display: flex; flex-direction: column; gap: 10px; margin-top: 8px; }
.example-card { background: var(--color-bg); border-radius: var(--radius-md); padding: 12px; }
.example-title { font-size: 13px; font-weight: 600; color: var(--color-text); margin-bottom: 6px; }
.example-detail { font-size: 12px; color: var(--color-text-secondary); line-height: 1.8; }
.example-detail code { background: rgba(99,102,241,0.08); color: var(--color-primary); padding: 1px 6px; border-radius: 4px; font-size: 12px; }
</style>

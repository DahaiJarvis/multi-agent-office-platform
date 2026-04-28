<!--
  知识库管理页面

  功能：
  - 知识库列表展示（卡片式布局，分页）
  - 创建知识库（弹窗表单）
  - 删除知识库（二次确认）
  - 点击卡片进入文档管理页
-->
<template>
  <div class="knowledge-page">
    <div class="page-header">
      <h2>知识库管理</h2>
      <button class="btn-primary" @click="showCreateForm = true">创建知识库</button>
    </div>

    <div v-if="showCreateForm" class="create-form-overlay" @click.self="showCreateForm = false">
      <div class="create-form">
        <h3>创建知识库</h3>
        <div class="form-group">
          <label>名称</label>
          <input v-model="createForm.name" type="text" placeholder="输入知识库名称" />
        </div>
        <div class="form-group">
          <label>描述</label>
          <textarea v-model="createForm.description" placeholder="输入知识库描述" rows="3" />
        </div>
        <div class="form-group">
          <label>访问级别</label>
          <select v-model="createForm.access_level">
            <option value="private">私有</option>
            <option value="team">团队</option>
            <option value="public">公开</option>
          </select>
        </div>
        <div class="form-actions">
          <button class="btn-secondary" @click="showCreateForm = false">取消</button>
          <button class="btn-primary" @click="handleCreate">创建</button>
        </div>
      </div>
    </div>

    <div v-if="loading" class="loading-state">加载中...</div>

    <div v-else-if="knowledgeBases.length === 0" class="empty-state">
      <p>暂无知识库，点击上方按钮创建</p>
    </div>

    <div v-else class="kb-grid">
      <div v-for="kb in knowledgeBases" :key="kb.id" class="kb-card" @click="goToKbDetail(kb.id)">
        <div class="kb-card-header">
          <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor" class="kb-icon">
            <path d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4z" />
          </svg>
          <h4>{{ kb.name }}</h4>
        </div>
        <p class="kb-desc">{{ kb.description || '暂无描述' }}</p>
        <div class="kb-meta">
          <span class="meta-tag">{{ kb.access_level === 'private' ? '私有' : kb.access_level === 'team' ? '团队' : '公开' }}</span>
          <span class="meta-count">{{ kb.document_count ?? 0 }} 文档</span>
        </div>
        <div class="kb-actions">
          <button class="btn-icon" @click.stop="handleDeleteKb(kb.id)" title="删除">
            <svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor">
              <path d="M5.5 1a.5.5 0 00-.5.5V2H2.5a.5.5 0 000 1h9a.5.5 0 000-1H9v-.5a.5.5 0 00-.5-.5h-3zM4 4.5a.5.5 0 01.5.5v6a.5.5 0 01-1 0V5a.5.5 0 01.5-.5zm3 0a.5.5 0 01.5.5v6a.5.5 0 01-1 0V5a.5.5 0 01.5-.5zm3 0a.5.5 0 01.5.5v6a.5.5 0 01-1 0V5a.5.5 0 01.5-.5z" />
            </svg>
          </button>
        </div>
      </div>
    </div>

    <div v-if="totalPages > 1" class="pagination">
      <button :disabled="page <= 1" @click="loadPage(page - 1)">上一页</button>
      <span>{{ page }} / {{ totalPages }}</span>
      <button :disabled="page >= totalPages" @click="loadPage(page + 1)">下一页</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { knowledgeApi, type KnowledgeBase } from '../../api/knowledge'

const router = useRouter()

const knowledgeBases = ref<KnowledgeBase[]>([])
const loading = ref(false)
const showCreateForm = ref(false)
const page = ref(1)
const total = ref(0)
const perPage = 20

const totalPages = computed(() => Math.ceil(total.value / perPage))

const createForm = ref({
  name: '',
  description: '',
  access_level: 'private',
})

async function loadKnowledgeBases() {
  loading.value = true
  try {
    const res = await knowledgeApi.listKnowledgeBases(page.value, perPage)
    const payload = res.data?.data || res.data
    knowledgeBases.value = payload?.knowledge_bases || payload?.items || []
    total.value = payload?.total || knowledgeBases.value.length
  } catch (err) {
    ElMessage.error('加载知识库列表失败')
  } finally {
    loading.value = false
  }
}

function loadPage(p: number) {
  page.value = p
  loadKnowledgeBases()
}

async function handleCreate() {
  if (!createForm.value.name.trim()) return
  try {
    await knowledgeApi.createKnowledgeBase(createForm.value)
    showCreateForm.value = false
    createForm.value = { name: '', description: '', access_level: 'private' }
    await loadKnowledgeBases()
  } catch (err) {
    ElMessage.error('创建知识库失败')
  }
}

async function handleDeleteKb(kbId: string) {
  if (!confirm('确定要删除此知识库吗？此操作不可恢复。')) return
  try {
    await knowledgeApi.deleteKnowledgeBase(kbId)
    await loadKnowledgeBases()
  } catch (err) {
    ElMessage.error('删除知识库失败')
  }
}

function goToKbDetail(kbId: string) {
  router.push({ name: 'KbDocs', params: { kbId } })
}

onMounted(() => {
  loadKnowledgeBases()
})
</script>

<style scoped>
.knowledge-page {
  max-width: 960px;
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
  color: var(--color-text);
}

.btn-primary {
  padding: 8px 18px;
  border-radius: var(--radius-md);
  background: var(--color-primary);
  color: white;
  font-size: 13px;
  font-weight: 600;
  transition: all var(--transition-fast);
}

.btn-primary:hover {
  background: var(--color-primary-dark);
}

.btn-secondary {
  padding: 8px 18px;
  border-radius: var(--radius-md);
  background: var(--color-bg);
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border);
  font-size: 13px;
  font-weight: 500;
  transition: all var(--transition-fast);
}

.btn-secondary:hover {
  border-color: var(--color-primary-light);
  color: var(--color-text);
}

.create-form-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
}

.create-form {
  background: var(--color-bg-elevated);
  border-radius: var(--radius-lg);
  padding: 28px;
  width: 420px;
  max-width: 90vw;
  box-shadow: var(--shadow-xl);
}

.create-form h3 {
  font-size: 18px;
  font-weight: 700;
  margin-bottom: 20px;
  color: var(--color-text);
}

.form-group {
  margin-bottom: 16px;
}

.form-group label {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-secondary);
  margin-bottom: 6px;
}

.form-group input,
.form-group textarea,
.form-group select {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: 14px;
  color: var(--color-text);
  background: var(--color-bg);
  transition: border-color var(--transition-fast);
}

.form-group input:focus,
.form-group textarea:focus,
.form-group select:focus {
  outline: none;
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px var(--color-primary-bg);
}

.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 20px;
}

.loading-state,
.empty-state {
  text-align: center;
  padding: 60px 20px;
  color: var(--color-text-tertiary);
  font-size: 14px;
}

.kb-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}

.kb-card {
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  padding: 20px;
  cursor: pointer;
  transition: all var(--transition-fast);
  position: relative;
}

.kb-card:hover {
  border-color: var(--color-primary-light);
  box-shadow: var(--shadow-md);
  transform: translateY(-2px);
}

.kb-card-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.kb-icon {
  color: var(--color-primary-light);
  flex-shrink: 0;
}

.kb-card-header h4 {
  font-size: 15px;
  font-weight: 700;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kb-desc {
  font-size: 13px;
  color: var(--color-text-secondary);
  line-height: 1.5;
  margin-bottom: 12px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.kb-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.meta-tag {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: var(--radius-full);
  background: var(--color-primary-bg);
  color: var(--color-primary);
}

.meta-count {
  font-size: 12px;
  color: var(--color-text-tertiary);
}

.kb-actions {
  position: absolute;
  top: 12px;
  right: 12px;
  opacity: 0;
  transition: opacity var(--transition-fast);
}

.kb-card:hover .kb-actions {
  opacity: 1;
}

.btn-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-sm);
  color: var(--color-text-tertiary);
  transition: all var(--transition-fast);
}

.btn-icon:hover {
  background: var(--color-danger-bg);
  color: var(--color-danger);
}

.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  margin-top: 24px;
  font-size: 13px;
  color: var(--color-text-secondary);
}

.pagination button {
  padding: 6px 14px;
  border-radius: var(--radius-md);
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border);
  font-size: 13px;
  color: var(--color-text-secondary);
  transition: all var(--transition-fast);
}

.pagination button:hover:not(:disabled) {
  border-color: var(--color-primary-light);
  color: var(--color-primary);
}

.pagination button:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
</style>

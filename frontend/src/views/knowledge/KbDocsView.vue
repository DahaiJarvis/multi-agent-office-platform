<!--
  知识库文档管理页面

  功能：
  - 文档列表展示（含处理状态、文件大小、上传时间）
  - 文档上传（点击/拖拽）
  - 文档删除（二次确认）
  - 分页浏览
-->
<template>
  <div class="kb-docs-page">
    <div class="page-header">
      <button class="btn-back" @click="router.push({ name: 'Knowledge' })">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
          <path d="M10 3L5 8l5 5" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round" />
        </svg>
        返回
      </button>
      <h2>{{ kbInfo.name || '知识库文档' }}</h2>
    </div>

    <div class="upload-area" @dragover.prevent @drop.prevent="handleDrop">
      <input
        ref="fileInput"
        type="file"
        multiple
        accept=".pdf,.docx,.doc,.xlsx,.xls,.md,.txt,.csv"
        style="display: none"
        @change="handleFileSelect"
      />
      <button class="btn-primary" @click="fileInput?.click()">上传文档</button>
      <span class="upload-hint">支持 PDF、Word、Excel、Markdown、TXT、CSV 格式</span>
    </div>

    <div v-if="uploading" class="upload-progress">上传中...</div>

    <div v-if="loading" class="loading-state">加载中...</div>

    <div v-else-if="documents.length === 0" class="empty-state">暂无文档，点击上方按钮上传</div>

    <div v-else class="doc-list">
      <div v-for="doc in documents" :key="doc.id" class="doc-item">
        <div class="doc-icon">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
            <path d="M4 0a2 2 0 00-2 2v12a2 2 0 002 2h8a2 2 0 002-2V4.586A2 2 0 0013.414 3L11 .586A2 2 0 009.586 0H4z" />
          </svg>
        </div>
        <div class="doc-info">
          <span class="doc-name">{{ doc.filename || doc.name }}</span>
          <span class="doc-meta">{{ formatSize(doc.file_size) }} | {{ formatDate(doc.created_at) }}</span>
        </div>
        <div class="doc-status" :class="doc.processing_status || 'completed'">
          {{ formatStatus(doc.processing_status) }}
        </div>
        <button class="btn-icon" @click="handleDeleteDoc(doc.id)" title="删除">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor">
            <path d="M5.5 1a.5.5 0 00-.5.5V2H2.5a.5.5 0 000 1h9a.5.5 0 000-1H9v-.5a.5.5 0 00-.5-.5h-3zM4 4.5a.5.5 0 01.5.5v6a.5.5 0 01-1 0V5a.5.5 0 01.5-.5zm3 0a.5.5 0 01.5.5v6a.5.5 0 01-1 0V5a.5.5 0 01.5-.5zm3 0a.5.5 0 01.5.5v6a.5.5 0 01-1 0V5a.5.5 0 01.5-.5z" />
          </svg>
        </button>
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
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { knowledgeApi, type KnowledgeBase, type Document as DocInfo } from '../../api/knowledge'

const route = useRoute()
const router = useRouter()
const kbId = route.params.kbId as string

const kbInfo = ref<Partial<KnowledgeBase>>({})
const documents = ref<DocInfo[]>([])
const loading = ref(false)
const uploading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const page = ref(1)
const total = ref(0)
const perPage = 20

const totalPages = computed(() => Math.ceil(total.value / perPage))

async function loadKbInfo() {
  try {
    const res = await knowledgeApi.getKnowledgeBase(kbId)
    kbInfo.value = res.data?.data || res.data || {}
  } catch (err) {
    ElMessage.error('加载知识库信息失败')
  }
}

async function loadDocuments() {
  loading.value = true
  try {
    const res = await knowledgeApi.listDocuments(kbId, page.value, perPage)
    const payload = res.data?.data || res.data
    documents.value = payload?.documents || payload?.items || []
    total.value = payload?.total || documents.value.length
  } catch (err) {
    ElMessage.error('加载文档列表失败')
  } finally {
    loading.value = false
  }
}

function loadPage(p: number) {
  page.value = p
  loadDocuments()
}

async function handleFileSelect(event: Event) {
  const input = event.target as HTMLInputElement
  if (!input.files?.length) return
  await uploadFiles(Array.from(input.files))
}

async function handleDrop(event: DragEvent) {
  if (!event.dataTransfer?.files?.length) return
  await uploadFiles(Array.from(event.dataTransfer.files))
}

async function uploadFiles(files: File[]) {
  uploading.value = true
  try {
    for (const file of files) {
      await knowledgeApi.uploadDocument(kbId, file)
    }
    await loadDocuments()
  } catch (err) {
    ElMessage.error('上传文档失败')
  } finally {
    uploading.value = false
  }
}

async function handleDeleteDoc(docId: string) {
  if (!confirm('确定要删除此文档吗？')) return
  try {
    await knowledgeApi.deleteDocument(kbId, docId)
    await loadDocuments()
  } catch (err) {
    ElMessage.error('删除文档失败')
  }
}

function formatSize(bytes: number | undefined): string {
  if (!bytes) return '-'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

function formatDate(dateStr: string | undefined): string {
  if (!dateStr) return '-'
  return new Date(dateStr).toLocaleDateString('zh-CN')
}

function formatStatus(status: string | undefined): string {
  const map: Record<string, string> = {
    pending: '待处理',
    processing: '处理中',
    completed: '已完成',
    failed: '失败',
  }
  return map[status || 'completed'] || status || '已完成'
}

onMounted(() => {
  loadKbInfo()
  loadDocuments()
})
</script>

<style scoped>
.kb-docs-page {
  max-width: 960px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 24px;
}

.btn-back {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 10px;
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
  font-size: 13px;
  transition: all var(--transition-fast);
}

.btn-back:hover {
  background: var(--color-bg);
  color: var(--color-text);
}

.page-header h2 {
  font-size: 20px;
  font-weight: 700;
  color: var(--color-text);
}

.upload-area {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px;
  border: 2px dashed var(--color-border);
  border-radius: var(--radius-lg);
  margin-bottom: 20px;
  transition: border-color var(--transition-fast);
}

.upload-area:hover {
  border-color: var(--color-primary-light);
}

.btn-primary {
  padding: 8px 18px;
  border-radius: var(--radius-md);
  background: var(--color-primary);
  color: white;
  font-size: 13px;
  font-weight: 600;
  transition: all var(--transition-fast);
  white-space: nowrap;
}

.btn-primary:hover {
  background: var(--color-primary-dark);
}

.upload-hint {
  font-size: 13px;
  color: var(--color-text-tertiary);
}

.upload-progress {
  text-align: center;
  padding: 12px;
  font-size: 13px;
  color: var(--color-primary);
}

.loading-state,
.empty-state {
  text-align: center;
  padding: 60px 20px;
  color: var(--color-text-tertiary);
  font-size: 14px;
}

.doc-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.doc-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  transition: all var(--transition-fast);
}

.doc-item:hover {
  border-color: var(--color-border);
}

.doc-icon {
  color: var(--color-primary-light);
  flex-shrink: 0;
}

.doc-info {
  flex: 1;
  min-width: 0;
}

.doc-name {
  display: block;
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-meta {
  font-size: 12px;
  color: var(--color-text-tertiary);
}

.doc-status {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: var(--radius-full);
  white-space: nowrap;
}

.doc-status.completed {
  background: var(--color-success-bg);
  color: var(--color-success);
}

.doc-status.processing {
  background: var(--color-warning-bg);
  color: var(--color-warning);
}

.doc-status.pending {
  background: var(--color-bg);
  color: var(--color-text-tertiary);
}

.doc-status.failed {
  background: var(--color-danger-bg);
  color: var(--color-danger);
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
  flex-shrink: 0;
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

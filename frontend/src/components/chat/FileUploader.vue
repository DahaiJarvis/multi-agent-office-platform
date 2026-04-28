<!--
  文件上传组件

  支持点击选择和拖拽上传，内置文件类型和大小校验。
  校验规则：
  - 允许的文件类型：pdf/docx/doc/xlsx/xls/md/txt/csv
  - 单文件最大 20MB
  - 最多同时上传 5 个文件
-->
<template>
  <div class="file-uploader" @dragover.prevent @drop.prevent="handleDrop">
    <input
      ref="fileInput"
      type="file"
      multiple
      accept=".pdf,.docx,.doc,.xlsx,.xls,.md,.txt,.csv"
      style="display: none"
      @change="handleFileSelect"
    />
    <button class="upload-trigger" @click="fileInput?.click()" title="上传文件">
      <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
        <path d="M.5 9.9a.5.5 0 01.5.5v2.5a1 1 0 001 1h12a1 1 0 001-1v-2.5a.5.5 0 011 0v2.5a2 2 0 01-2 2H2a2 2 0 01-2-2v-2.5a.5.5 0 01.5-.5z"/>
        <path d="M7.646 1.146a.5.5 0 01.708 0l3 3a.5.5 0 01-.708.708L8.5 2.707V11.5a.5.5 0 01-1 0V2.707L5.354 4.854a.5.5 0 11-.708-.708l3-3z"/>
      </svg>
    </button>
    <div v-if="files.length" class="file-list">
      <div v-for="f in files" :key="f.name" class="file-item">
        <span class="file-name">{{ f.name }}</span>
        <button class="file-remove" @click="removeFile(f.name)">
          <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor">
            <path d="M3.5 3.5l5 5M8.5 3.5l-5 5" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round" />
          </svg>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

/** 允许上传的文件扩展名集合 */
const ALLOWED_EXTENSIONS = new Set(['pdf', 'docx', 'doc', 'xlsx', 'xls', 'md', 'txt', 'csv'])

/** 单文件最大尺寸（字节），默认 20MB */
const MAX_FILE_SIZE = 20 * 1024 * 1024

/** 最大同时上传文件数 */
const MAX_FILE_COUNT = 5

const fileInput = ref<HTMLInputElement | null>(null)
const files = ref<File[]>([])

const emit = defineEmits<{
  (e: 'upload', files: File[]): void
}>()

/**
 * 校验文件是否满足上传条件
 * @param file 待校验的文件
 * @returns 校验通过返回 true，否则返回 false
 */
function validateFile(file: File): boolean {
  const ext = file.name.split('.').pop()?.toLowerCase() || ''
  if (!ALLOWED_EXTENSIONS.has(ext)) {
    alert(`不支持的文件类型: .${ext}，仅支持 ${Array.from(ALLOWED_EXTENSIONS).join('/')}`)
    return false
  }
  if (file.size > MAX_FILE_SIZE) {
    const sizeMB = (file.size / 1024 / 1024).toFixed(1)
    alert(`文件 ${file.name} 超过 20MB 限制（当前 ${sizeMB}MB）`)
    return false
  }
  return true
}

/**
 * 处理文件选择事件
 * @param event 文件选择事件
 */
function handleFileSelect(event: Event) {
  const input = event.target as HTMLInputElement
  if (!input.files) return

  const newFiles = Array.from(input.files).filter(validateFile)
  const remaining = MAX_FILE_COUNT - files.value.length
  if (remaining <= 0) {
    alert(`最多同时上传 ${MAX_FILE_COUNT} 个文件`)
    return
  }

  const toAdd = newFiles.slice(0, remaining)
  if (toAdd.length < newFiles.length) {
    alert(`已达到最大文件数限制，仅添加了前 ${toAdd.length} 个文件`)
  }

  files.value = [...files.value, ...toAdd]
  emit('upload', files.value)

  // 重置 input 以允许重复选择同一文件
  input.value = ''
}

/**
 * 处理文件拖拽事件
 * @param event 拖拽事件
 */
function handleDrop(event: DragEvent) {
  if (!event.dataTransfer?.files) return

  const newFiles = Array.from(event.dataTransfer.files).filter(validateFile)
  const remaining = MAX_FILE_COUNT - files.value.length
  if (remaining <= 0) {
    alert(`最多同时上传 ${MAX_FILE_COUNT} 个文件`)
    return
  }

  const toAdd = newFiles.slice(0, remaining)
  if (toAdd.length < newFiles.length) {
    alert(`已达到最大文件数限制，仅添加了前 ${toAdd.length} 个文件`)
  }

  files.value = [...files.value, ...toAdd]
  emit('upload', files.value)
}

/**
 * 移除已选文件
 * @param name 文件名
 */
function removeFile(name: string) {
  files.value = files.value.filter((f) => f.name !== name)
  emit('upload', files.value)
}
</script>

<style scoped>
.file-uploader {
  display: flex;
  align-items: center;
  gap: 6px;
}

.upload-trigger {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
  transition: all var(--transition-fast);
}

.upload-trigger:hover {
  background: var(--color-bg);
  color: var(--color-primary);
}

.file-list {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.file-item {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: var(--radius-full);
  background: var(--color-primary-bg);
  font-size: 11px;
  color: var(--color-primary);
}

.file-name {
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-remove {
  display: flex;
  align-items: center;
  color: var(--color-text-tertiary);
  transition: color var(--transition-fast);
}

.file-remove:hover {
  color: var(--color-danger);
}
</style>

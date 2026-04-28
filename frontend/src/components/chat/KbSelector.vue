<!--
  知识库选择器组件

  用于聊天界面中选择关联的知识库，支持：
  - 下拉选择已有知识库
  - 点击外部自动关闭下拉
  - 组件挂载时自动加载知识库列表
-->
<template>
  <div class="kb-selector">
    <div class="selector-trigger" @click="showDropdown = !showDropdown">
      <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" class="selector-icon">
        <path d="M2 2a1 1 0 011-1h4.586a1 1 0 01.707.293l.707.707H13a1 1 0 011 1v2a1 1 0 01-1 1H3a1 1 0 01-1-1V2zm0 6a1 1 0 011-1h10a1 1 0 011 1v5a1 1 0 01-1 1H3a1 1 0 01-1-1V8z" />
      </svg>
      <span class="selector-text">{{ selectedLabel }}</span>
      <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor" class="arrow-icon">
        <path d="M3 5l3 3 3-3" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round" />
      </svg>
    </div>
    <div v-if="showDropdown" class="selector-dropdown">
      <div
        class="dropdown-item"
        :class="{ active: !selectedKbId }"
        @click="selectKb('')"
      >
        不使用知识库
      </div>
      <div
        v-for="kb in knowledgeBases"
        :key="kb.id"
        class="dropdown-item"
        :class="{ active: selectedKbId === kb.id }"
        @click="selectKb(kb.id)"
      >
        {{ kb.name }}
      </div>
      <div v-if="loading" class="dropdown-loading">加载中...</div>
      <div v-if="!loading && knowledgeBases.length === 0" class="dropdown-empty">暂无知识库</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { knowledgeApi, type KnowledgeBase as KbInfo } from '../../api/knowledge'

const selectedKbId = ref('')
const knowledgeBases = ref<KbInfo[]>([])
const showDropdown = ref(false)
const loading = ref(false)

const emit = defineEmits<{
  (e: 'select', kbId: string): void
}>()

const selectedLabel = computed(() => {
  if (!selectedKbId.value) return '选择知识库'
  const kb = knowledgeBases.value.find((k) => k.id === selectedKbId.value)
  return kb ? kb.name : '选择知识库'
})

function selectKb(kbId: string) {
  selectedKbId.value = kbId
  showDropdown.value = false
  emit('select', kbId)
}

function handleClickOutside(e: MouseEvent) {
  const target = e.target as HTMLElement
  if (!target.closest('.kb-selector')) {
    showDropdown.value = false
  }
}

onMounted(async () => {
  document.addEventListener('click', handleClickOutside)
  loading.value = true
  try {
    const res = await knowledgeApi.listKnowledgeBases()
    const payload = res.data?.data || res.data
    knowledgeBases.value = payload?.knowledge_bases || payload?.items || []
  } catch (err) {
    ElMessage.error('加载知识库列表失败')
  } finally {
    loading.value = false
  }
})

onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside)
})
</script>

<style scoped>
.kb-selector {
  position: relative;
}

.selector-trigger {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: var(--radius-md);
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  cursor: pointer;
  font-size: 13px;
  color: var(--color-text-secondary);
  transition: all var(--transition-fast);
  white-space: nowrap;
}

.selector-trigger:hover {
  border-color: var(--color-primary-light);
  color: var(--color-text);
}

.selector-icon {
  flex-shrink: 0;
  color: var(--color-primary-light);
}

.selector-text {
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.arrow-icon {
  flex-shrink: 0;
  opacity: 0.5;
}

.selector-dropdown {
  position: absolute;
  bottom: calc(100% + 6px);
  left: 0;
  min-width: 200px;
  max-height: 240px;
  overflow-y: auto;
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
  z-index: 100;
}

.dropdown-item {
  padding: 8px 14px;
  font-size: 13px;
  color: var(--color-text);
  cursor: pointer;
  transition: background var(--transition-fast);
}

.dropdown-item:hover {
  background: var(--color-primary-bg);
}

.dropdown-item.active {
  color: var(--color-primary);
  font-weight: 600;
  background: var(--color-primary-bg);
}

.dropdown-loading,
.dropdown-empty {
  padding: 12px 14px;
  font-size: 13px;
  color: var(--color-text-tertiary);
  text-align: center;
}
</style>

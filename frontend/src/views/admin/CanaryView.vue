<template>
  <div class="canary-page">
    <div class="page-header">
      <h2>灰度发布</h2>
      <div class="header-actions">
        <button class="btn-refresh" @click="loadFlags">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
            <path fill-rule="evenodd" d="M8 3a5 5 0 110 10A5 5 0 018 3zm0 1.5a3.5 3.5 0 100 7 3.5 3.5 0 000-7z" opacity="0.4" />
            <path d="M8 1a7 7 0 016.95 6.25h-1.5A5.5 5.5 0 008 2.5a5.5 5.5 0 00-5.45 4.75h-1.5A7 7 0 018 1z" />
          </svg>
          刷新
        </button>
      </div>
    </div>

    <div class="guide-banner" v-if="!guideDismissed">
      <div class="guide-banner-content">
        <span class="guide-icon">?</span>
        <span>灰度发布通过功能开关控制新特性的上线范围，支持按比例灰度和白名单机制，降低发布风险。</span>
        <button class="guide-link" @click="showGuideDialog = true">查看详细指南</button>
        <button class="guide-close" @click="guideDismissed = true">&times;</button>
      </div>
    </div>

    <div v-if="loading" class="loading-state">
      <div class="spinner" />
      <span>加载中...</span>
    </div>

    <div v-else-if="flags.length === 0" class="empty-state">
      <p>暂无灰度功能开关</p>
    </div>

    <div v-else class="flag-list">
      <div v-for="flag in flags" :key="flag.name" class="flag-card">
        <div class="flag-header">
          <div class="flag-title-row">
            <span class="flag-name">{{ flag.name }}</span>
            <label class="toggle-switch">
              <input
                type="checkbox"
                :checked="flag.enabled"
                @change="toggleFlag(flag.name, ($event.target as HTMLInputElement).checked)"
              />
              <span class="toggle-slider" />
            </label>
          </div>
          <p v-if="flag.description" class="flag-desc">{{ flag.description }}</p>
        </div>

        <div class="flag-config">
          <div class="config-row">
            <span class="config-label">灰度比例</span>
            <div class="inline-hint">控制新特性对多少比例的用户生效，0%=关闭，100%=全量发布</div>
            <div class="slider-row">
              <input
                type="range"
                min="0"
                max="100"
                :value="flag.rollout_percentage || 0"
                class="range-slider"
                @change="updateRollout(flag.name, +($event.target as HTMLInputElement).value)"
              />
              <span class="range-value">{{ flag.rollout_percentage || 0 }}%</span>
            </div>
          </div>

          <div class="config-row">
            <span class="config-label">白名单</span>
            <div class="inline-hint">白名单用户不受灰度比例限制，始终可以使用该功能</div>
            <div class="whitelist-row">
              <span v-for="uid in (flag.whitelist || [])" :key="uid" class="whitelist-tag">
                {{ uid }}
                <button class="tag-remove" @click="removeFromWhitelist(flag.name, uid)">&times;</button>
              </span>
              <div class="add-whitelist">
                <input
                  v-model="whitelistInputs[flag.name]"
                  class="whitelist-input"
                  placeholder="添加用户ID"
                  @keydown.enter="addToWhitelist(flag.name)"
                />
                <button class="btn-add" @click="addToWhitelist(flag.name)">添加</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <el-dialog v-model="showGuideDialog" title="灰度发布使用指南" width="640px">
      <div class="guide-content">
        <div class="guide-section">
          <h4>什么是灰度发布？</h4>
          <p>灰度发布（Canary Release）是一种渐进式发布策略，通过功能开关控制新特性对用户的可见范围。先让小部分用户体验新功能，确认无问题后再逐步扩大范围，降低发布风险。</p>
        </div>
        <div class="guide-section">
          <h4>使用流程</h4>
          <div class="flow-steps">
            <div class="flow-step">
              <div class="step-num">1</div>
              <div class="step-content">
                <strong>开启开关</strong>
                <p>打开功能开关，启用该灰度特性</p>
              </div>
            </div>
            <div class="flow-step">
              <div class="step-num">2</div>
              <div class="step-content">
                <strong>设置灰度比例</strong>
                <p>从小比例开始（如 5%），逐步观察效果</p>
              </div>
            </div>
            <div class="flow-step">
              <div class="step-num">3</div>
              <div class="step-content">
                <strong>添加白名单</strong>
                <p>将内部测试用户加入白名单，优先验证功能</p>
              </div>
            </div>
            <div class="flow-step">
              <div class="step-num">4</div>
              <div class="step-content">
                <strong>逐步放量</strong>
                <p>确认无异常后逐步提高比例，直至 100% 全量发布</p>
              </div>
            </div>
          </div>
        </div>
        <div class="guide-section">
          <h4>配置说明</h4>
          <div class="config-list">
            <div class="config-item"><code>功能开关</code> - 总开关，关闭后该特性对所有用户不可见</div>
            <div class="config-item"><code>灰度比例</code> - 0-100%，控制随机命中的用户比例</div>
            <div class="config-item"><code>白名单</code> - 指定用户ID，白名单用户始终可以使用该功能</div>
          </div>
        </div>
        <div class="guide-section">
          <h4>注意事项</h4>
          <p>灰度比例和白名单是叠加关系：白名单用户始终可用，其余用户按灰度比例随机命中。关闭开关后，即使白名单用户也无法使用。</p>
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
import { ElMessage } from 'element-plus'
import { adminApi } from '../../api/admin'

const loading = ref(false)
const flags = ref<any[]>([])
const whitelistInputs = reactive<Record<string, string>>({})
const guideDismissed = ref(false)
const showGuideDialog = ref(false)

async function loadFlags() {
  loading.value = true
  try {
    const data = await adminApi.canaryFlags()
    flags.value = data.flags || data || []
  } catch {
    flags.value = []
  } finally {
    loading.value = false
  }
}

async function toggleFlag(name: string, enabled: boolean) {
  try {
    await adminApi.canaryToggle(name, enabled)
    const flag = flags.value.find((f) => f.name === name)
    if (flag) flag.enabled = enabled
  } catch {
    ElMessage.error('切换灰度开关失败')
  }
}

async function updateRollout(name: string, percentage: number) {
  try {
    await adminApi.canaryRollout(name, percentage)
    const flag = flags.value.find((f) => f.name === name)
    if (flag) flag.rollout_percentage = percentage
  } catch {
    ElMessage.error('更新灰度比例失败')
  }
}

async function addToWhitelist(name: string) {
  const userId = whitelistInputs[name]?.trim()
  if (!userId) return

  const flag = flags.value.find((f) => f.name === name)
  if (!flag) return

  const newList = [...(flag.whitelist || []), userId]
  try {
    await adminApi.canaryWhitelist(name, newList)
    flag.whitelist = newList
    whitelistInputs[name] = ''
  } catch {
    ElMessage.error('添加白名单失败')
  }
}

async function removeFromWhitelist(name: string, userId: string) {
  const flag = flags.value.find((f) => f.name === name)
  if (!flag) return

  const newList = (flag.whitelist || []).filter((id: string) => id !== userId)
  try {
    await adminApi.canaryWhitelist(name, newList)
    flag.whitelist = newList
  } catch {
    ElMessage.error('移除白名单失败')
  }
}

onMounted(loadFlags)
</script>

<style scoped>
.canary-page {
  max-width: 800px;
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
}

.btn-refresh {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  color: var(--color-text-secondary);
  font-size: 13px;
  font-weight: 500;
  transition: all var(--transition-fast);
}

.btn-refresh:hover {
  border-color: var(--color-primary-light);
  color: var(--color-primary);
  background: var(--color-primary-bg);
}

.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 60px;
  color: var(--color-text-secondary);
}

.spinner {
  width: 20px;
  height: 20px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

.empty-state {
  text-align: center;
  padding: 60px;
  color: var(--color-text-secondary);
}

.flag-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.flag-card {
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  padding: 20px 24px;
}

.flag-header {
  margin-bottom: 16px;
}

.flag-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.flag-name {
  font-size: 15px;
  font-weight: 700;
  color: var(--color-text);
  font-family: var(--font-mono);
}

.flag-desc {
  font-size: 13px;
  color: var(--color-text-secondary);
  margin-top: 4px;
}

.toggle-switch {
  position: relative;
  display: inline-block;
  width: 44px;
  height: 24px;
}

.toggle-switch input {
  opacity: 0;
  width: 0;
  height: 0;
}

.toggle-slider {
  position: absolute;
  cursor: pointer;
  inset: 0;
  background: var(--color-border);
  border-radius: 24px;
  transition: background var(--transition-fast);
}

.toggle-slider::before {
  content: '';
  position: absolute;
  width: 18px;
  height: 18px;
  left: 3px;
  bottom: 3px;
  background: white;
  border-radius: 50%;
  transition: transform var(--transition-fast);
}

.toggle-switch input:checked + .toggle-slider {
  background: var(--color-primary);
}

.toggle-switch input:checked + .toggle-slider::before {
  transform: translateX(20px);
}

.flag-config {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--color-border-light);
}

.config-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.config-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.slider-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.range-slider {
  flex: 1;
  height: 6px;
  -webkit-appearance: none;
  appearance: none;
  background: var(--color-border);
  border-radius: var(--radius-full);
  outline: none;
}

.range-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--color-primary);
  cursor: pointer;
  box-shadow: var(--shadow-sm);
}

.range-value {
  font-size: 14px;
  font-weight: 700;
  font-family: var(--font-mono);
  color: var(--color-primary);
  min-width: 44px;
  text-align: right;
}

.whitelist-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.whitelist-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border-radius: var(--radius-full);
  background: var(--color-primary-bg);
  color: var(--color-primary);
  font-size: 12px;
  font-weight: 500;
}

.tag-remove {
  font-size: 14px;
  color: var(--color-primary);
  opacity: 0.6;
  transition: opacity var(--transition-fast);
}

.tag-remove:hover {
  opacity: 1;
}

.add-whitelist {
  display: flex;
  gap: 6px;
}

.whitelist-input {
  padding: 5px 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-size: 12px;
  outline: none;
  width: 120px;
  transition: border-color var(--transition-fast);
}

.whitelist-input:focus {
  border-color: var(--color-primary);
}

.btn-add {
  padding: 5px 10px;
  border-radius: var(--radius-sm);
  background: var(--color-primary);
  color: white;
  font-size: 12px;
  font-weight: 500;
  transition: background var(--transition-fast);
}

.btn-add:hover {
  background: var(--color-primary-dark);
}
.header-actions { display: flex; gap: 10px; }
.btn-outline { padding: 7px 14px; border-radius: var(--radius-md); border: 1px solid var(--color-primary); color: var(--color-primary); font-weight: 600; font-size: 13px; transition: all var(--transition-fast); }
.btn-outline:hover { background: rgba(99,102,241,0.06); }
.guide-banner { background: rgba(99,102,241,0.06); border: 1px solid rgba(99,102,241,0.15); border-radius: var(--radius-lg); padding: 12px 16px; margin-bottom: 16px; }
.guide-banner-content { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--color-text-secondary); line-height: 1.5; }
.guide-icon { width: 22px; height: 22px; border-radius: 50%; background: var(--color-primary); color: white; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; flex-shrink: 0; }
.guide-link { color: var(--color-primary); font-weight: 500; cursor: pointer; white-space: nowrap; }
.guide-link:hover { text-decoration: underline; }
.guide-close { margin-left: auto; color: var(--color-text-tertiary); font-size: 18px; cursor: pointer; padding: 0 4px; line-height: 1; }
.guide-close:hover { color: var(--color-text); }
.inline-hint { font-size: 12px; color: var(--color-text-tertiary); margin-bottom: 8px; line-height: 1.5; }
.guide-content { max-height: 60vh; overflow-y: auto; }
.guide-section { margin-bottom: 20px; }
.guide-section h4 { font-size: 15px; font-weight: 600; color: var(--color-text); margin: 0 0 8px; }
.guide-section p { font-size: 13px; color: var(--color-text-secondary); line-height: 1.6; margin: 0 0 8px; }
.guide-section code { background: rgba(99,102,241,0.08); color: var(--color-primary); padding: 1px 6px; border-radius: 4px; font-size: 12px; }
.flow-steps { display: flex; flex-direction: column; gap: 12px; margin-top: 8px; }
.flow-step { display: flex; gap: 12px; align-items: flex-start; }
.step-num { width: 24px; height: 24px; border-radius: 50%; background: var(--color-primary); color: white; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; flex-shrink: 0; }
.step-content strong { font-size: 13px; color: var(--color-text); }
.step-content p { font-size: 12px; color: var(--color-text-secondary); margin: 2px 0 0; }
.config-list { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
.config-item { font-size: 13px; color: var(--color-text-secondary); line-height: 1.6; }
.config-item code { background: rgba(99,102,241,0.08); color: var(--color-primary); padding: 1px 6px; border-radius: 4px; font-size: 12px; }
</style>

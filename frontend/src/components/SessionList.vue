<template>
  <div class="session-list">
    <div class="session-header">
      <div class="header-copy">
        <span class="title">我的会话</span>
        <span class="subtitle">Conversations</span>
      </div>
      <el-button type="primary" :icon="Plus" size="small" @click="$emit('create')">
        新建会话
      </el-button>
    </div>

    <div class="summary-card">
      <div class="summary-title">当前案情摘要</div>
      <div class="summary-text">
        {{ activeSummary || '当前会话暂无案情摘要。' }}
      </div>
    </div>

    <div class="search-row">
      <el-input
        v-model="localSearchKeyword"
        clearable
        placeholder="搜索会话、案情摘要..."
      >
        <template #prefix>
          <el-icon><Search /></el-icon>
        </template>
      </el-input>
    </div>

    <div class="batch-toolbar">
      <el-button
        size="small"
        :type="batchMode ? 'default' : 'primary'"
        plain
        @click="toggleBatchMode"
      >
        {{ batchMode ? '取消批量管理' : '批量管理' }}
      </el-button>

      <template v-if="batchMode">
        <el-button
          size="small"
          text
          @click="toggleSelectAll"
          :disabled="!sessions.length"
        >
          {{ allSelected ? '取消全选' : '全选当前列表' }}
        </el-button>
        <el-button
          size="small"
          type="danger"
          :disabled="!selectedIds.length"
          @click="requestBatchDelete"
        >
          删除已选 ({{ selectedIds.length }})
        </el-button>
      </template>
    </div>

    <div v-if="loading" class="list-status">正在搜索会话...</div>

    <el-scrollbar height="calc(100vh - 260px)">
      <template v-if="sessions.length">
        <div
          v-for="session in sessions"
          :key="session.id"
          :class="['session-item', { active: session.id === currentId, selectable: batchMode }]"
          @click="handleSessionClick(session.id)"
        >
          <div class="session-row top-row">
            <div class="session-name-group">
              <el-checkbox
                v-if="batchMode"
                :model-value="selectedIds.includes(session.id)"
                @click.stop
                @change="toggleSelection(session.id)"
              />
              <el-icon class="session-icon"><ChatDotRound /></el-icon>
              <span class="session-name">{{ session.title || `未命名会话 #${session.id}` }}</span>
            </div>
            <el-tag size="small" effect="plain" class="type-tag">
              {{ session.case_type || '未分类' }}
            </el-tag>
          </div>

          <div class="session-preview">
            {{ buildPreview(session) }}
          </div>

          <div class="session-meta">
            <span class="time">{{ formatTime(session.updated_at || session.created_at) }}</span>
            <div v-if="!batchMode" class="action-group">
              <el-button
                :icon="Edit"
                size="small"
                link
                @click.stop="$emit('rename', session)"
              />
              <el-button
                type="danger"
                :icon="Delete"
                size="small"
                link
                @click.stop="$emit('delete', session.id)"
              />
            </div>
          </div>
        </div>
      </template>

      <el-empty
        v-else
        :description="emptyText || '暂无会话'"
        :image-size="60"
      />
    </el-scrollbar>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ChatDotRound, Delete, Edit, Plus, Search } from '@element-plus/icons-vue'

const props = defineProps({
  sessions: { type: Array, default: () => [] },
  currentId: { type: [String, Number], default: '' },
  activeSummary: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  searchKeyword: { type: String, default: '' },
  batchMode: { type: Boolean, default: false },
  emptyText: { type: String, default: '暂无会话' },
})

const emit = defineEmits([
  'create',
  'switch',
  'delete',
  'rename',
  'search',
  'batch-mode-change',
  'batch-delete',
])

const localSearchKeyword = ref(props.searchKeyword)
const selectedIds = ref([])
let searchTimer = null

watch(
  () => props.searchKeyword,
  (value) => {
    localSearchKeyword.value = value
  }
)

watch(localSearchKeyword, (value) => {
  const normalized = String(value || '').trim()
  const current = String(props.searchKeyword || '').trim()

  if (normalized === current) {
    return
  }

  if (searchTimer) {
    clearTimeout(searchTimer)
  }

  if (!normalized) {
    emit('search', '')
    return
  }

  searchTimer = setTimeout(() => {
    emit('search', normalized)
  }, 300)
})

watch(
  () => props.sessions,
  (sessions) => {
    const validIds = new Set((sessions || []).map((session) => session.id))
    selectedIds.value = selectedIds.value.filter((id) => validIds.has(id))
  },
  { deep: true }
)

watch(
  () => props.batchMode,
  (enabled) => {
    if (!enabled) {
      selectedIds.value = []
    }
  }
)

onBeforeUnmount(() => {
  if (searchTimer) {
    clearTimeout(searchTimer)
  }
})

const allSelected = computed(() =>
  props.sessions.length > 0 &&
  props.sessions.every((session) => selectedIds.value.includes(session.id))
)

function toggleBatchMode() {
  emit('batch-mode-change', !props.batchMode)
}

function toggleSelection(sessionId) {
  if (selectedIds.value.includes(sessionId)) {
    selectedIds.value = selectedIds.value.filter((id) => id !== sessionId)
    return
  }
  selectedIds.value = [...selectedIds.value, sessionId]
}

function toggleSelectAll() {
  if (allSelected.value) {
    selectedIds.value = []
    return
  }
  selectedIds.value = props.sessions.map((session) => session.id)
}

function requestBatchDelete() {
  emit('batch-delete', [...selectedIds.value])
}

function handleSessionClick(sessionId) {
  if (props.batchMode) {
    toggleSelection(sessionId)
    return
  }
  emit('switch', sessionId)
}

function buildPreview(session) {
  const summary = String(session?.case_summary || '').trim()
  if (!summary) {
    return '普通咨询'
  }
  return summary.length > 40 ? `${summary.slice(0, 40)}...` : summary
}

function formatTime(value) {
  if (!value) return ''
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}
</script>

<style scoped>
.session-list {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.session-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.header-copy {
  display: flex;
  flex-direction: column;
}

.title {
  font-weight: 700;
  font-size: 15px;
  color: #303133;
}

.subtitle {
  font-size: 11px;
  color: #909399;
}

.summary-card {
  margin-bottom: 12px;
  padding: 12px;
  border-radius: 10px;
  background: #f7f9fc;
  border: 1px solid #e5e7eb;
}

.summary-title {
  font-size: 12px;
  font-weight: 700;
  color: #374151;
  margin-bottom: 6px;
}

.summary-text {
  font-size: 12px;
  line-height: 1.6;
  color: #6b7280;
  white-space: pre-wrap;
}

.search-row {
  margin-bottom: 10px;
}

.batch-toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}

.list-status {
  margin-bottom: 8px;
  font-size: 12px;
  color: #6b7280;
}

.session-item {
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
  margin-bottom: 6px;
  border: 1px solid transparent;
}

.session-item:hover {
  background: #ecf5ff;
}

.session-item.selectable {
  border-color: #e5e7eb;
}

.session-item.active {
  background: #409eff;
  color: #fff;
}

.session-item.active .session-preview,
.session-item.active .time {
  color: rgba(255, 255, 255, 0.78);
}

.session-item.active .type-tag {
  color: #409eff;
  background: rgba(255, 255, 255, 0.92);
  border-color: transparent;
}

.session-item.active .action-group :deep(.el-button) {
  color: rgba(255, 255, 255, 0.9);
}

.session-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.top-row {
  margin-bottom: 6px;
}

.session-name-group {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.session-icon {
  flex: 0 0 auto;
}

.session-name {
  font-size: 13px;
  font-weight: 600;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.type-tag {
  flex: 0 0 auto;
  color: #6b7280;
}

.session-preview {
  font-size: 12px;
  line-height: 1.5;
  color: #6b7280;
  margin-bottom: 8px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}

.action-group {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}

.time {
  font-size: 11px;
  color: #909399;
}
</style>

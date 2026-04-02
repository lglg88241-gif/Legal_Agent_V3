<template>
  <div class="session-list">
    <div class="session-header">
      <div class="header-copy">
        <span class="title">我的案件卷宗</span>
        <span class="subtitle">My Legal Cases</span>
      </div>
      <el-button type="primary" :icon="Plus" size="small" @click="$emit('create')">
        新建卷宗
      </el-button>
    </div>

    <div class="summary-card">
      <div class="summary-title">卷宗净白摘要</div>
      <div class="summary-text">{{ activeSummary || '当前卷宗暂无摘要，案情补充后会在这里展示。' }}</div>
    </div>

    <el-scrollbar height="calc(100vh - 200px)">
      <div
        v-for="session in sessions"
        :key="session.id"
        :class="['session-item', { active: session.id === currentId }]"
        @click="$emit('switch', session.id)"
      >
        <div class="session-name">
          <el-icon><ChatDotRound /></el-icon>
          <span>{{ session.title || `新卷宗 #${session.id}` }}</span>
        </div>
        <div class="session-meta">
          <span class="time">{{ formatTime(session.updated_at || session.created_at) }}</span>
          <div class="action-group">
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

      <el-empty v-if="!sessions.length" description="暂无案件卷宗" :image-size="60" />
    </el-scrollbar>
  </div>
</template>

<script setup>
import { ChatDotRound, Delete, Edit, Plus } from '@element-plus/icons-vue'

defineProps({
  sessions: { type: Array, default: () => [] },
  currentId: { type: [String, Number], default: '' },
  activeSummary: { type: String, default: '' },
})

defineEmits(['create', 'switch', 'delete', 'rename'])

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

.session-item {
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
  margin-bottom: 4px;
}

.session-item:hover {
  background: #ecf5ff;
}

.session-item.active {
  background: #409eff;
  color: #fff;
}

.session-item.active .time {
  color: rgba(255, 255, 255, 0.7);
}

.session-item.active .subtitle {
  color: rgba(255, 255, 255, 0.74);
}

.session-item.active .action-group :deep(.el-button) {
  color: rgba(255, 255, 255, 0.86);
}

.session-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  margin-bottom: 6px;
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

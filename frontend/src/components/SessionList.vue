<template>
  <div class="session-list">
    <div class="session-header">
      <span class="title">Conversations</span>
      <el-button type="primary" :icon="Plus" size="small" circle @click="$emit('create')" />
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
          <span>{{ session.title || `Conversation ${session.id}` }}</span>
        </div>
        <div class="session-meta">
          <span class="time">{{ formatTime(session.created_at) }}</span>
          <el-button
            type="danger"
            :icon="Delete"
            size="small"
            link
            @click.stop="$emit('delete', session.id)"
          />
        </div>
      </div>

      <el-empty v-if="!sessions.length" description="No conversations" :image-size="60" />
    </el-scrollbar>
  </div>
</template>

<script setup>
import { ChatDotRound, Delete, Plus } from '@element-plus/icons-vue'

defineProps({
  sessions: { type: Array, default: () => [] },
  currentId: { type: [String, Number], default: '' },
})

defineEmits(['create', 'switch', 'delete'])

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
  margin-bottom: 12px;
}

.title {
  font-weight: 600;
  font-size: 15px;
  color: #303133;
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
}

.time {
  font-size: 11px;
  color: #909399;
}
</style>

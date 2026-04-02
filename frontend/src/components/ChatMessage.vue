<template>
  <div :class="['chat-message', isUser ? 'user-msg' : 'bot-msg']">
    <div class="avatar">
      <el-avatar :size="36" :style="{ background: isUser ? '#409eff' : '#67c23a' }">
        <el-icon v-if="isUser"><User /></el-icon>
        <el-icon v-else><Service /></el-icon>
      </el-avatar>
    </div>

    <div class="bubble-wrap">
      <div class="meta">
        <span class="name">{{ isUser ? 'User' : 'Assistant' }}</span>
        <span class="time">{{ displayTime }}</span>
      </div>

      <div v-if="agentActions.length" class="agent-actions">
        <div class="action-header">
          <el-icon><Operation /></el-icon>
          <span>Agent Pipeline</span>
        </div>

        <div v-for="(action, index) in agentActions" :key="index" class="action-item">
          <el-tag size="small" :type="toolTagType(action.tool)" effect="dark" class="tool-tag">
            {{ toolLabel(action.tool) }}
          </el-tag>
          <span class="action-summary">{{ action.result_summary }}</span>
        </div>
      </div>

      <div class="bubble" v-html="formatted"></div>

      <div v-if="normalizedSources.length" class="rag-sources">
        <span class="sources-label">Sources</span>
        <el-popover
          v-for="(source, index) in normalizedSources"
          :key="`${source.full}-${index}`"
          placement="top"
          trigger="click"
          :width="260"
        >
          <template #reference>
            <el-tag size="small" type="info" effect="plain" class="source-tag" round>
              {{ source.label }}
            </el-tag>
          </template>
          <div class="source-card">
            <div class="source-card-title">Reference file</div>
            <div class="source-card-name">{{ source.full }}</div>
          </div>
        </el-popover>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Operation, Service, User } from '@element-plus/icons-vue'

const props = defineProps({
  msg: { type: Object, required: true },
})

const isUser = computed(() => props.msg.role === 'user')

const displayTime = computed(() => {
  if (!props.msg.time) return ''
  return props.msg.time
})

const formatted = computed(() => {
  if (!props.msg.content) return ''
  return props.msg.content
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\n/g, '<br>')
})

const agentActions = computed(() => {
  return Array.isArray(props.msg.agentActions) ? props.msg.agentActions : []
})

const normalizedSources = computed(() => {
  if (!Array.isArray(props.msg.sources)) return []

  return props.msg.sources
    .map((source) => {
      if (typeof source === 'string') {
        return {
          full: source,
          label: source.length > 24 ? `${source.slice(0, 24)}...` : source,
        }
      }

      if (source && typeof source === 'object') {
        const full = source.source || source.name || source.domain || ''
        if (!full) return null
        return {
          full,
          label: full.length > 24 ? `${full.slice(0, 24)}...` : full,
        }
      }

      return null
    })
    .filter(Boolean)
})

const toolLabels = {
  tool_router: 'Router',
  retrieval_tools: 'Retrieval',
  legal_advisor: 'Advisor',
}

const toolTagTypes = {
  tool_router: 'primary',
  retrieval_tools: 'warning',
  legal_advisor: 'success',
}

function toolLabel(name) {
  return toolLabels[name] || name
}

function toolTagType(name) {
  return toolTagTypes[name] || ''
}
</script>

<style scoped>
.chat-message {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
  max-width: 85%;
}

.user-msg {
  flex-direction: row-reverse;
  margin-left: auto;
}

.bot-msg {
  margin-right: auto;
}

.bubble-wrap {
  flex: 1;
  min-width: 0;
}

.meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
  font-size: 12px;
  color: #909399;
}

.user-msg .meta {
  flex-direction: row-reverse;
}

.name {
  font-weight: 500;
  color: #606266;
}

.agent-actions {
  margin-bottom: 8px;
  padding: 8px 12px;
  background: #f0f9eb;
  border-radius: 8px;
  border: 1px solid #e1f3d8;
}

.action-header {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  font-weight: 600;
  color: #67c23a;
  margin-bottom: 6px;
}

.action-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 0;
  font-size: 12px;
}

.tool-tag {
  flex-shrink: 0;
}

.action-summary {
  color: #606266;
  font-size: 12px;
}

.bubble {
  padding: 12px 16px;
  border-radius: 12px;
  line-height: 1.6;
  font-size: 14px;
  word-break: break-word;
}

.user-msg .bubble {
  background: #409eff;
  color: #fff;
  border-top-right-radius: 4px;
}

.bot-msg .bubble {
  background: #f4f4f5;
  color: #303133;
  border-top-left-radius: 4px;
}

.rag-sources {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.sources-label {
  font-size: 11px;
  color: #909399;
}

.source-tag {
  font-size: 11px;
  cursor: pointer;
}

.source-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.source-card-title {
  font-size: 12px;
  color: #909399;
}

.source-card-name {
  font-size: 13px;
  color: #303133;
  word-break: break-all;
}
</style>

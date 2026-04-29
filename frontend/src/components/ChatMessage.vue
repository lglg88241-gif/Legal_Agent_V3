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
        <span class="name">{{ isUser ? '我' : '法律助手' }}</span>
        <span class="time">{{ displayTime }}</span>
        <span v-if="props.msg.pending" class="pending-flag">发送中...</span>
      </div>

      <div class="bubble" v-html="formatted"></div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Service, User } from '@element-plus/icons-vue'

const props = defineProps({
  msg: { type: Object, required: true },
})

const isUser = computed(() => props.msg.role === 'user')
const displayTime = computed(() => props.msg.time || '')

const formatted = computed(() => {
  if (!props.msg.content) return ''
  return props.msg.content
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\n/g, '<br>')
})
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

.pending-flag {
  color: #e6a23c;
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
</style>

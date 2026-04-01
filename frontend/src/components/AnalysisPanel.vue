<template>
  <div class="analysis-panel">
    <div class="panel-section">
      <div class="section-title">系统状态</div>
      <div class="info-row">
        <span class="label">整体状态</span>
        <el-tag :type="status.ready ? 'success' : 'warning'" size="small">
          {{ status.ready ? '系统就绪' : '初始化中' }}
        </el-tag>
      </div>
      <div class="info-row">
        <span class="label">知识库文档</span>
        <span class="value">{{ status.docCount || 0 }}</span>
      </div>
      <div class="info-row">
        <span class="label">路由模型</span>
        <span class="value">{{ status.model || '-' }}</span>
      </div>
    </div>

    <div class="panel-section">
      <div class="section-title">组件连通性</div>
      <div v-for="(value, key) in status.components" :key="key" class="info-row">
        <span class="label">{{ componentLabel(key) }}</span>
        <el-tag :type="value === 'connected' ? 'success' : 'info'" size="small">
          {{ value === 'connected' ? 'connected' : 'loading' }}
        </el-tag>
      </div>
    </div>

    <div class="panel-section">
      <div class="section-title">检索工具</div>
      <div v-if="status.tools?.length" class="tools-list">
        <el-tag
          v-for="tool in status.tools"
          :key="tool"
          size="small"
          effect="plain"
          class="tool-item"
        >
          {{ tool }}
        </el-tag>
      </div>
      <span v-else class="value">-</span>
    </div>

    <div class="panel-section">
      <div class="section-title">本轮分析</div>
      <div v-if="analysis.actions?.length">
        <div v-for="(action, i) in analysis.actions" :key="i" class="action-detail">
          <div class="action-tool">
            <el-tag size="small" effect="dark">{{ action.tool }}</el-tag>
          </div>
          <div class="action-result">
            <span class="detail-label">结果:</span>
            <span class="detail-value">{{ action.result_summary }}</span>
          </div>
        </div>
      </div>
      <div v-else class="info-row">
        <span class="value muted">等待对话...</span>
      </div>
    </div>

    <div class="panel-section">
      <div class="section-title">评价反馈</div>
      <div class="rating-row">
        <el-rate v-model="rating" :max="5" />
      </div>
      <el-input
        v-model="comment"
        type="textarea"
        :rows="2"
        placeholder="对本次回答的建议..."
        size="small"
      />
      <el-button type="primary" size="small" style="margin-top: 8px; width: 100%" @click="submit">
        提交反馈
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

defineProps({
  status: {
    type: Object,
    default: () => ({
      ready: false,
      docCount: 0,
      model: '',
      tools: [],
      components: {},
    }),
  },
  analysis: {
    type: Object,
    default: () => ({ actions: [], sources: [] }),
  },
})

const emit = defineEmits(['feedback'])

const rating = ref(0)
const comment = ref('')

function componentLabel(key) {
  const labels = {
    chroma: 'Chroma',
    neo4j: 'Neo4j',
    llm: '本地 LLM',
    celery: 'Celery',
  }
  return labels[key] || key
}

function submit() {
  if (!rating.value) return
  emit('feedback', { rating: rating.value, comment: comment.value })
  rating.value = 0
  comment.value = ''
}
</script>

<style scoped>
.panel-section {
  margin-bottom: 20px;
}

.section-title {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 10px;
  padding-bottom: 6px;
  border-bottom: 1px solid #ebeef5;
}

.info-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 4px 0;
  font-size: 13px;
}

.label {
  color: #909399;
}

.value {
  color: #303133;
  text-align: right;
  max-width: 60%;
  font-size: 12px;
}

.muted {
  color: #909399;
}

.tools-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.tool-item {
  font-size: 11px;
}

.action-detail {
  padding: 8px;
  margin-bottom: 8px;
  background: #fafafa;
  border-radius: 6px;
  border: 1px solid #f0f0f0;
}

.action-tool {
  margin-bottom: 4px;
}

.detail-label {
  font-size: 11px;
  color: #909399;
  margin-right: 4px;
}

.detail-value {
  font-size: 11px;
  color: #606266;
  word-break: break-word;
}

.rating-row {
  margin-bottom: 8px;
}
</style>

<template>
  <div :class="['analysis-panel', { collapsed }]">
    <div class="panel-toolbar">
      <div v-if="!collapsed" class="toolbar-title">
        <span class="eyebrow">Neo4j Live</span>
        <span class="headline">知识图谱侧边栏</span>
      </div>
      <el-tooltip :content="collapsed ? '展开侧栏' : '收起侧栏'" placement="left">
        <el-button circle size="small" @click="$emit('toggle-sidebar')">
          <el-icon><component :is="collapsed ? Expand : Fold" /></el-icon>
        </el-button>
      </el-tooltip>
    </div>

    <div v-if="collapsed" class="collapsed-state">
      <el-icon class="collapsed-icon"><Share /></el-icon>
      <span class="collapsed-text">图谱</span>
    </div>

    <template v-else>
      <div class="panel-section graph-first">
        <div class="section-title">实时子图</div>
        <KnowledgeGraphPanel :graph="graph" />
      </div>

      <div class="panel-section">
        <div class="section-title">系统状态</div>
        <div class="info-row">
          <span class="label">整体状态</span>
          <el-tag :type="status.ready ? 'success' : 'warning'" size="small">
            {{ status.ready ? '系统就绪' : '初始化中' }}
          </el-tag>
        </div>
        <div class="info-row">
          <span class="label">知识文档数</span>
          <span class="value">{{ status.docCount || 0 }}</span>
        </div>
        <div class="info-row">
          <span class="label">当前模型</span>
          <span class="value">{{ status.model || '-' }}</span>
        </div>
      </div>

      <div class="panel-section">
        <div class="section-title">组件状态</div>
        <div v-for="(value, key) in status.components" :key="key" class="info-row">
          <span class="label">{{ componentLabel(key) }}</span>
          <el-tag :type="value === 'connected' ? 'success' : 'info'" size="small">
            {{ value === 'connected' ? '已连接' : '加载中' }}
          </el-tag>
        </div>
      </div>

      <div class="panel-section">
        <div class="section-title">可用工具</div>
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
          <div v-for="(action, index) in analysis.actions" :key="index" class="action-detail">
            <div class="action-tool">
              <el-tag size="small" effect="dark">{{ action.tool }}</el-tag>
            </div>
            <div class="action-result">
              <span class="detail-label">结果</span>
              <span class="detail-value">{{ action.result_summary }}</span>
            </div>
          </div>
        </div>
        <div v-else class="info-row">
          <span class="value muted">等待对话触发检索...</span>
        </div>
      </div>

      <div class="panel-section">
        <div class="section-title">参考来源</div>
        <div v-if="normalizedSources.length" class="sources-list">
          <el-tag v-for="source in normalizedSources" :key="source" size="small" effect="plain" type="info">
            {{ source }}
          </el-tag>
        </div>
        <div v-else class="info-row">
          <span class="value muted">当前回答尚未返回来源标签</span>
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
          placeholder="请输入你对本轮回答的建议"
          size="small"
        />
        <el-button type="primary" size="small" class="submit-btn" @click="submit">
          提交反馈
        </el-button>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Expand, Fold, Share } from '@element-plus/icons-vue'
import KnowledgeGraphPanel from './KnowledgeGraphPanel.vue'

const props = defineProps({
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
  graph: {
    type: Object,
    default: () => ({ query: '', nodes: [], edges: [], loading: false, error: '' }),
  },
  collapsed: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['feedback', 'toggle-sidebar'])

const rating = ref(0)
const comment = ref('')

const normalizedSources = computed(() =>
  (props.analysis.sources || [])
    .map((source) => {
      if (typeof source === 'string') return source
      if (source && typeof source === 'object') return source.source || source.name || ''
      return ''
    })
    .filter(Boolean)
)

function componentLabel(key) {
  const labels = {
    chroma: 'Chroma',
    neo4j: 'Neo4j',
    llm: 'LLM',
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
.analysis-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 16px;
  overflow-y: auto;
}

.analysis-panel.collapsed {
  align-items: center;
}

.panel-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}

.toolbar-title {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.eyebrow {
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #64748b;
}

.headline {
  font-size: 16px;
  font-weight: 700;
  color: #0f172a;
}

.collapsed-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: #475569;
}

.collapsed-icon {
  font-size: 22px;
}

.collapsed-text {
  writing-mode: vertical-rl;
  font-size: 12px;
  letter-spacing: 0.2em;
}

.panel-section {
  border-radius: 18px;
  background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
  border: 1px solid #e5e7eb;
  padding: 14px;
}

.graph-first {
  background:
    radial-gradient(circle at top right, rgba(99, 102, 241, 0.12), transparent 30%),
    linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
}

.section-title {
  font-size: 13px;
  font-weight: 700;
  color: #1f2937;
  margin-bottom: 12px;
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
  color: #6b7280;
}

.value {
  color: #111827;
  text-align: right;
  max-width: 62%;
  font-size: 12px;
}

.muted {
  color: #94a3b8;
}

.tools-list,
.sources-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.tool-item {
  font-size: 11px;
}

.action-detail {
  padding: 10px;
  margin-bottom: 8px;
  background: rgba(248, 250, 252, 0.95);
  border-radius: 10px;
  border: 1px solid #edf2f7;
}

.action-tool {
  margin-bottom: 6px;
}

.detail-label {
  font-size: 11px;
  color: #6b7280;
  margin-right: 6px;
}

.detail-value {
  font-size: 12px;
  color: #334155;
  word-break: break-word;
}

.rating-row {
  margin-bottom: 8px;
}

.submit-btn {
  margin-top: 8px;
  width: 100%;
}
</style>

<template>
  <div :class="['analysis-panel', { collapsed }]">
    <div class="panel-toolbar">
      <div v-if="!collapsed" class="toolbar-title">
        <span class="eyebrow">Neo4j Live</span>
        <span class="headline">Knowledge Graph Panel</span>
      </div>
      <el-tooltip :content="collapsed ? 'Expand sidebar' : 'Collapse sidebar'" placement="left">
        <el-button circle size="small" @click="$emit('toggle-sidebar')">
          <el-icon><component :is="collapsed ? Expand : Fold" /></el-icon>
        </el-button>
      </el-tooltip>
    </div>

    <div v-if="collapsed" class="collapsed-state">
      <el-icon class="collapsed-icon"><Share /></el-icon>
      <span class="collapsed-text">Graph</span>
    </div>

    <template v-else>
      <div class="panel-section graph-first">
        <div class="section-title">Live Subgraph</div>
        <KnowledgeGraphPanel :graph="graph" />
      </div>

      <div class="panel-section">
        <div class="section-title">System Status</div>
        <div class="info-row">
          <span class="label">Overall</span>
          <el-tag :type="status.ready ? 'success' : 'warning'" size="small">
            {{ status.ready ? 'Ready' : 'Loading' }}
          </el-tag>
        </div>
        <div class="info-row">
          <span class="label">Knowledge docs</span>
          <span class="value">{{ status.docCount || 0 }}</span>
        </div>
        <div class="info-row">
          <span class="label">Model</span>
          <span class="value">{{ status.model || '-' }}</span>
        </div>
      </div>

      <div class="panel-section">
        <div class="section-title">Components</div>
        <div v-for="(value, key) in status.components" :key="key" class="info-row">
          <span class="label">{{ componentLabel(key) }}</span>
          <el-tag :type="value === 'connected' ? 'success' : 'info'" size="small">
            {{ value === 'connected' ? 'connected' : 'loading' }}
          </el-tag>
        </div>
      </div>

      <div class="panel-section">
        <div class="section-title">Tools</div>
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
        <div class="section-title">Current Analysis</div>
        <div v-if="analysis.actions?.length">
          <div v-for="(action, index) in analysis.actions" :key="index" class="action-detail">
            <div class="action-tool">
              <el-tag size="small" effect="dark">{{ action.tool }}</el-tag>
            </div>
            <div class="action-result">
              <span class="detail-label">Result</span>
              <span class="detail-value">{{ action.result_summary }}</span>
            </div>
          </div>
        </div>
        <div v-else class="info-row">
          <span class="value muted">Waiting for a retrieval cycle.</span>
        </div>
      </div>

      <div class="panel-section">
        <div class="section-title">Sources</div>
        <div v-if="normalizedSources.length" class="sources-list">
          <el-tag v-for="source in normalizedSources" :key="source" size="small" effect="plain" type="info">
            {{ source }}
          </el-tag>
        </div>
        <div v-else class="info-row">
          <span class="value muted">No source labels in the latest answer.</span>
        </div>
      </div>

      <div class="panel-section">
        <div class="section-title">Feedback</div>
        <div class="rating-row">
          <el-rate v-model="rating" :max="5" />
        </div>
        <el-input
          v-model="comment"
          type="textarea"
          :rows="2"
          placeholder="Share feedback for the latest answer"
          size="small"
        />
        <el-button type="primary" size="small" class="submit-btn" @click="submit">
          Submit Feedback
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

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
        <div class="system-component-list">
          <div
            v-for="entry in systemComponentEntries"
            :key="entry.key"
            class="system-component-item"
          >
            <span class="label">{{ entry.label }}</span>
            <el-tag :type="entry.status === 'connected' ? 'success' : 'info'" size="small">
              {{ entry.status === 'connected' ? '已连接' : '加载中' }}
            </el-tag>
          </div>
        </div>
      </div>

      <div class="panel-section">
        <div class="section-title">组件状态</div>
        <div class="execution-list">
          <div
            v-for="entry in executionComponentEntries"
            :key="entry.key"
            class="execution-item"
          >
            <div class="execution-header">
              <strong>{{ entry.label }}</strong>
              <el-tag :type="executionStatusTagType(entry.status)" size="small">
                {{ executionStatusLabel(entry.status) }}
              </el-tag>
            </div>
            <div class="execution-detail">{{ entry.detail }}</div>
          </div>
        </div>
      </div>

      <div class="panel-section">
        <div class="section-title">本轮分析</div>
        <div class="analysis-meta-grid">
          <div class="analysis-meta-card">
            <span>请求状态</span>
            <strong>{{ requestStateLabel }}</strong>
          </div>
          <div class="analysis-meta-card">
            <span>路由结果</span>
            <strong>{{ routeIntentLabel }}</strong>
          </div>
          <div class="analysis-meta-card">
            <span>响应耗时</span>
            <strong>{{ latencyLabel }}</strong>
          </div>
          <div class="analysis-meta-card">
            <span>置信度</span>
            <strong>{{ confidenceLabel }}</strong>
          </div>
        </div>

        <div v-if="analysis.requestError" class="analysis-warning">
          {{ analysis.requestError }}
        </div>

        <div v-if="analysisTimeline.length" class="timeline-list">
          <div
            v-for="(item, index) in analysisTimeline"
            :key="`${item.key}-${index}`"
            class="timeline-item"
          >
            <div class="timeline-header">
              <strong>{{ item.label }}</strong>
              <el-tag :type="executionStatusTagType(item.status)" size="small" effect="plain">
                {{ executionStatusLabel(item.status) }}
              </el-tag>
            </div>
            <div class="timeline-summary">{{ item.summary }}</div>
          </div>
        </div>
        <div v-else class="info-row">
          <span class="value muted">等待当前会话请求触发后端执行状态...</span>
        </div>

        <div class="analysis-subsection">
          <div class="subsection-title">参考来源</div>
          <div v-if="normalizedSources.length" class="tag-list">
            <el-tag
              v-for="source in normalizedSources"
              :key="source"
              size="small"
              effect="plain"
              type="info"
            >
              {{ source }}
            </el-tag>
          </div>
          <div v-else class="subsection-empty">当前请求尚未返回参考来源</div>
        </div>

        <div class="analysis-subsection">
          <div class="subsection-title">待补充信息</div>
          <div v-if="normalizedMissingInfo.length" class="tag-list">
            <el-tag
              v-for="item in normalizedMissingInfo"
              :key="item"
              size="small"
              effect="plain"
              type="warning"
            >
              {{ item }}
            </el-tag>
          </div>
          <div v-else class="subsection-empty">当前请求未提示额外缺失信息</div>
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
    default: () => ({
      actions: [],
      sources: [],
      routeIntent: '',
      latencyMs: null,
      confidenceScore: null,
      missingInfo: [],
      requestState: 'idle',
      requestError: '',
      updatedAt: '',
      graphSyncStatus: 'idle',
      graphSyncSummary: '',
      graphSyncUpdatedAt: '',
    }),
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

const executionDefinitions = [
  { key: 'tool_router', label: 'router' },
  { key: 'fact_updater', label: 'fact_updater' },
  { key: 'retrieval_tools', label: 'retrieval_tools' },
  { key: 'reflector', label: 'reflector' },
  { key: 'legal_advisor', label: 'advisor' },
  { key: 'graph_sync', label: 'graph_sync' },
  { key: 'dossier_intelligence', label: 'dossier_intelligence' },
  { key: 'summary_update', label: 'summary_update' },
  { key: 'participant_resolution', label: 'participant_resolution' },
]

const normalizedSources = computed(() =>
  (props.analysis.sources || [])
    .map((source) => {
      if (typeof source === 'string') return source
      if (source && typeof source === 'object') return source.source || source.name || ''
      return ''
    })
    .filter(Boolean)
)

const normalizedMissingInfo = computed(() =>
  (props.analysis.missingInfo || [])
    .map((item) => String(item || '').trim())
    .filter(Boolean)
)

const systemComponentEntries = computed(() =>
  Object.entries(props.status.components || {}).map(([key, value]) => ({
    key,
    label: componentLabel(key),
    status: value,
  }))
)

const actionLookup = computed(() => {
  const lookup = new Map()
  for (const action of props.analysis.actions || []) {
    const key = normalizeKey(action?.tool)
    if (!key) continue
    lookup.set(key, action)
  }
  return lookup
})

const routeIntentKey = computed(() => normalizeKey(props.analysis.routeIntent))

const requestStateLabel = computed(() =>
  executionStatusLabel(normalizeKey(props.analysis.requestState) || 'idle')
)

const routeIntentLabel = computed(() => {
  switch (routeIntentKey.value) {
    case 'case_update':
      return '案情更新'
    case 'general_consult':
      return '一般咨询'
    case 'greeting':
      return '问候'
    case 'out_of_scope':
      return '超出范围'
    default:
      return '未返回'
  }
})

const latencyLabel = computed(() => {
  const latency = Number(props.analysis.latencyMs)
  return Number.isFinite(latency) && latency > 0 ? `${Math.round(latency)} ms` : '暂无'
})

const confidenceLabel = computed(() => {
  const confidence = Number(props.analysis.confidenceScore)
  return Number.isFinite(confidence) && confidence > 0 ? confidence.toFixed(2) : '暂无'
})

const executionComponentEntries = computed(() =>
  executionDefinitions.map((definition) => buildExecutionEntry(definition))
)

const analysisTimeline = computed(() => {
  const items = []
  for (const action of props.analysis.actions || []) {
    const key = normalizeKey(action?.tool)
    if (!key) continue
    items.push({
      key,
      label: executionLabelForTool(key),
      status: 'success',
      summary: normalizeText(action?.result_summary) || '已完成后端执行',
    })
  }

  const graphSyncStatus = normalizeKey(props.analysis.graphSyncStatus)
  const graphSyncSummary = normalizeText(props.analysis.graphSyncSummary)
  if (graphSyncStatus && graphSyncStatus !== 'idle') {
    items.push({
      key: 'graph_sync',
      label: executionLabelForTool('graph_sync'),
      status: graphSyncStatus,
      summary: graphSyncSummary || '图谱同步状态已更新',
    })
  }

  if (!items.length && normalizeKey(props.analysis.requestState) === 'running') {
    items.push({
      key: 'request',
      label: 'request',
      status: 'running',
      summary: '主请求处理中，等待后端节点返回执行结果',
    })
  }

  return items
})

function normalizeText(value) {
  return String(value || '').trim()
}

function normalizeKey(value) {
  return normalizeText(value).toLowerCase()
}

function componentLabel(key) {
  const labels = {
    chroma: 'Chroma',
    neo4j: 'Neo4j',
    llm: 'LLM',
    celery: 'Celery',
  }
  return labels[key] || key
}

function executionLabelForTool(key) {
  const labels = {
    request: 'request',
    tool_router: 'router',
    fact_updater: 'fact_updater',
    retrieval_tools: 'retrieval_tools',
    reflector: 'reflector',
    legal_advisor: 'advisor',
    greeting: 'greeting',
    out_of_scope: 'out_of_scope',
    graph_sync: 'graph_sync',
    dossier_intelligence: 'dossier_intelligence',
    summary_update: 'summary_update',
    participant_resolution: 'participant_resolution',
  }
  return labels[key] || key || 'unknown'
}

function executionStatusLabel(status) {
  switch (normalizeKey(status)) {
    case 'success':
      return '成功'
    case 'running':
      return '执行中'
    case 'pending':
      return '待执行'
    case 'warning':
      return '需关注'
    case 'error':
      return '失败'
    case 'skipped':
      return '已跳过'
    default:
      return '空闲'
  }
}

function executionStatusTagType(status) {
  switch (normalizeKey(status)) {
    case 'success':
      return 'success'
    case 'running':
      return 'warning'
    case 'warning':
      return 'warning'
    case 'error':
      return 'danger'
    case 'pending':
      return 'info'
    case 'skipped':
      return 'info'
    default:
      return ''
  }
}

function buildExecutionEntry(definition) {
  const key = definition.key
  const requestState = normalizeKey(props.analysis.requestState)
  const routeIntent = routeIntentKey.value
  const action = actionLookup.value.get(key)

  if (key === 'graph_sync') {
    const graphSyncStatus = normalizeKey(props.analysis.graphSyncStatus) || 'idle'
    return {
      key,
      label: definition.label,
      status: graphSyncStatus,
      detail: normalizeText(props.analysis.graphSyncSummary) || defaultIdleDetail(key),
    }
  }

  if (action) {
    return {
      key,
      label: definition.label,
      status: 'success',
      detail: normalizeText(action?.result_summary) || '本轮已执行',
    }
  }

  if (requestState === 'running') {
    if (key === 'tool_router') {
      return {
        key,
        label: definition.label,
        status: 'running',
        detail: '正在判定当前会话请求的路由',
      }
    }
    return {
      key,
      label: definition.label,
      status: 'pending',
      detail: '等待上游节点结果',
    }
  }

  if (requestState === 'error') {
    if (key === 'tool_router') {
      return {
        key,
        label: definition.label,
        status: 'error',
        detail: '主请求失败，未完整返回后端执行结果',
      }
    }
    return {
      key,
      label: definition.label,
      status: 'idle',
      detail: defaultIdleDetail(key),
    }
  }

  if (requestState === 'success') {
    return {
      key,
      label: definition.label,
      status: shouldSkipForRoute(key, routeIntent) ? 'skipped' : 'idle',
      detail: skippedOrIdleDetail(key, routeIntent),
    }
  }

  return {
    key,
    label: definition.label,
    status: 'idle',
    detail: defaultIdleDetail(key),
  }
}

function shouldSkipForRoute(key, routeIntent) {
  if (!routeIntent) return false
  if (routeIntent === 'greeting' || routeIntent === 'out_of_scope') {
    return key !== 'tool_router'
  }
  if (routeIntent === 'general_consult') {
    return ['fact_updater', 'reflector', 'summary_update', 'participant_resolution'].includes(key)
  }
  if (routeIntent === 'case_update') {
    return ['legal_advisor', 'dossier_intelligence', 'summary_update', 'participant_resolution'].includes(key)
  }
  return false
}

function skippedOrIdleDetail(key, routeIntent) {
  if (shouldSkipForRoute(key, routeIntent)) {
    return '当前请求路由下未触发该后端模块'
  }
  return defaultIdleDetail(key)
}

function defaultIdleDetail(key) {
  if (key === 'dossier_intelligence') {
    return '案卷级智能整理不属于当前会话主请求链路'
  }
  if (key === 'summary_update') {
    return '当前请求未触发摘要更新任务'
  }
  if (key === 'participant_resolution') {
    return '当前请求未触发主体确认写入任务'
  }
  if (key === 'graph_sync') {
    return '当前请求未触发图谱同步状态更新'
  }
  return '等待当前会话请求触发'
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

.subsection-title {
  font-size: 12px;
  font-weight: 600;
  color: #334155;
  margin-bottom: 8px;
}

.info-row,
.system-component-item {
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

.muted,
.subsection-empty {
  color: #94a3b8;
}

.system-component-list,
.execution-list,
.timeline-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.execution-item,
.timeline-item {
  padding: 10px;
  background: rgba(248, 250, 252, 0.95);
  border-radius: 12px;
  border: 1px solid #e2e8f0;
}

.execution-header,
.timeline-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}

.execution-header strong,
.timeline-header strong {
  font-size: 12px;
  color: #0f172a;
}

.execution-detail,
.timeline-summary {
  margin-top: 6px;
  font-size: 12px;
  color: #475569;
  line-height: 1.5;
}

.analysis-meta-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}

.analysis-meta-card {
  padding: 10px;
  border-radius: 12px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
}

.analysis-meta-card span {
  display: block;
  font-size: 11px;
  color: #64748b;
  margin-bottom: 4px;
}

.analysis-meta-card strong {
  font-size: 12px;
  color: #0f172a;
}

.analysis-warning {
  margin-bottom: 12px;
  padding: 10px 12px;
  border-radius: 12px;
  background: #fff7ed;
  border: 1px solid #fdba74;
  color: #9a3412;
  font-size: 12px;
  line-height: 1.5;
}

.analysis-subsection {
  margin-top: 14px;
}

.tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.rating-row {
  margin-bottom: 8px;
}

.submit-btn {
  margin-top: 8px;
  width: 100%;
}
</style>

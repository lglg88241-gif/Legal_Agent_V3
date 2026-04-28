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

      <div class="panel-section panel-section-compact">
        <div class="section-title-row">
          <div>
            <div class="section-title section-title-tight">组件状态</div>
            <div class="section-description">只突出当前请求真正触发的关键模块。</div>
          </div>
          <span class="section-summary">{{ componentStatusSummary }}</span>
        </div>

        <div class="component-status-list">
          <div
            v-for="entry in coreExecutionEntries"
            :key="entry.key"
            :class="['component-status-row', `status-${normalizeKey(entry.status)}`]"
          >
            <div class="component-status-copy">
              <strong>{{ entry.label }}</strong>
              <span>
                {{ showExecutionDetail(entry) ? entry.detail : executionStatusQuietLabel(entry.status) }}
              </span>
            </div>
            <div class="component-status-meta">
              <span :class="['status-dot', `status-${normalizeKey(entry.status)}`]"></span>
              <span class="component-status-badge">{{ executionStatusQuietLabel(entry.status) }}</span>
            </div>
          </div>
        </div>

        <div v-if="secondaryExecutionEntries.length" class="component-secondary-group">
          <div class="compact-subsection-title">更多组件状态</div>
          <div class="component-inline-list">
            <div
              v-for="entry in secondaryExecutionEntries"
              :key="`secondary-${entry.key}`"
              :class="['component-inline-chip', `status-${normalizeKey(entry.status)}`]"
            >
              <span>{{ entry.label }}</span>
              <span>{{ executionStatusQuietLabel(entry.status) }}</span>
            </div>
          </div>
        </div>

        <div v-if="highlightedExecutionEntries.length" class="component-note-list">
          <div
            v-for="entry in highlightedExecutionEntries"
            :key="`note-${entry.key}`"
            class="component-note"
          >
            <strong>{{ entry.fullLabel }}</strong>
            <span>{{ entry.detail }}</span>
          </div>
        </div>
      </div>

      <div class="panel-section analysis-section">
        <div class="section-title-row">
          <div>
            <div class="section-title section-title-tight">本轮分析</div>
            <div class="section-description">当前会话请求的处理过程与结果摘要。</div>
          </div>
        </div>

        <div v-if="!hasAnalysisContent" class="analysis-empty-state">
          <div class="analysis-empty-copy">
            <strong>等待你发送问题后，我会在这里展示本轮分析过程。</strong>
            <span>系统会按请求类型自动安排处理链路，并同步关键执行状态。</span>
          </div>
          <div class="analysis-empty-hints">
            <span class="analysis-empty-hint">理解案情</span>
            <span class="analysis-empty-hint">检索依据</span>
            <span class="analysis-empty-hint">更新图谱</span>
          </div>
        </div>

        <template v-else>
          <div class="analysis-meta-grid">
            <div class="analysis-meta-card">
              <span>请求状态</span>
              <strong>{{ requestStateLabel }}</strong>
            </div>
            <div class="analysis-meta-card">
              <span>任务分流</span>
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

          <div v-if="normalizedSources.length" class="analysis-subsection">
            <div class="subsection-title">参考来源</div>
            <div class="tag-list">
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
          </div>

          <div v-if="normalizedMissingInfo.length" class="analysis-subsection">
            <div class="subsection-title">待补充信息</div>
            <div class="tag-list">
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
          </div>
        </template>
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
  { key: 'tool_router', label: '任务分流', fullLabel: '任务分流' },
  { key: 'fact_updater', label: '案情理解', fullLabel: '案情理解' },
  { key: 'retrieval_tools', label: '依据检索', fullLabel: '依据检索' },
  { key: 'reflector', label: '风险审查', fullLabel: '风险审查' },
  { key: 'legal_advisor', label: '答复生成', fullLabel: '答复生成' },
  { key: 'graph_sync', label: '图谱更新', fullLabel: '图谱更新' },
  { key: 'dossier_intelligence', label: '案卷整理', fullLabel: '案卷整理' },
  { key: 'summary_update', label: '摘要生成', fullLabel: '摘要生成' },
  { key: 'participant_resolution', label: '主体识别', fullLabel: '主体识别' },
]

const coreExecutionKeys = ['fact_updater', 'retrieval_tools', 'graph_sync']

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

const coreExecutionEntries = computed(() =>
  executionComponentEntries.value.filter((entry) => coreExecutionKeys.includes(entry.key))
)

const secondaryExecutionEntries = computed(() =>
  executionComponentEntries.value.filter(
    (entry) =>
      !coreExecutionKeys.includes(entry.key)
      && !['idle', 'skipped'].includes(normalizeKey(entry.status))
  )
)

const highlightedExecutionEntries = computed(() =>
  executionComponentEntries.value
    .filter((entry) => !['idle', 'skipped'].includes(normalizeKey(entry.status)))
    .slice(0, 3)
)

const componentStatusSummary = computed(() => {
  const entries = executionComponentEntries.value
  const running = entries.filter((entry) => normalizeKey(entry.status) === 'running').length
  const success = entries.filter((entry) => normalizeKey(entry.status) === 'success').length
  const warning = entries.filter((entry) => ['warning', 'error'].includes(normalizeKey(entry.status))).length

  if (running > 0) return `${running} 个模块处理中`
  if (warning > 0) return `${warning} 个模块需关注`
  if (success > 0) return `${success} 个模块已完成`
  return '等待问题触发'
})

const hasAnalysisContent = computed(() => {
  const requestState = normalizeKey(props.analysis.requestState)
  if (requestState && requestState !== 'idle') return true
  if (analysisTimeline.value.length) return true
  if (normalizeText(props.analysis.requestError)) return true
  if (routeIntentKey.value) return true
  if (normalizedSources.value.length || normalizedMissingInfo.value.length) return true

  const latency = Number(props.analysis.latencyMs)
  if (Number.isFinite(latency) && latency > 0) return true

  const confidence = Number(props.analysis.confidenceScore)
  return Number.isFinite(confidence) && confidence > 0
})

const analysisTimeline = computed(() => {
  const items = []
  for (const action of props.analysis.actions || []) {
    const key = normalizeKey(action?.tool)
    if (!key) continue
    items.push({
      key,
      label: executionLabelForTool(key),
      status: 'success',
      summary: normalizeText(action?.result_summary) || '本轮已完成执行',
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
      label: '请求处理',
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
    request: '请求处理',
    tool_router: '任务分流',
    fact_updater: '案情理解',
    retrieval_tools: '依据检索',
    reflector: '风险审查',
    legal_advisor: '答复生成',
    greeting: '问候响应',
    out_of_scope: '范围拦截',
    graph_sync: '图谱更新',
    dossier_intelligence: '案卷整理',
    summary_update: '摘要生成',
    participant_resolution: '主体识别',
  }
  return labels[key] || key || '未知组件'
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

function executionStatusCompactLabel(status) {
  switch (normalizeKey(status)) {
    case 'success':
      return '已完成'
    case 'running':
      return '进行中'
    case 'pending':
      return '待执行'
    case 'warning':
      return '关注'
    case 'error':
      return '失败'
    case 'skipped':
      return '跳过'
    default:
      return '待命'
  }
}

function executionStatusQuietLabel(status) {
  switch (normalizeKey(status)) {
    case 'success':
      return '已完成'
    case 'running':
      return '进行中'
    case 'pending':
      return '等待中'
    case 'warning':
      return '需关注'
    case 'error':
      return '失败'
    case 'skipped':
      return '本轮未涉及'
    default:
      return '待命'
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
      fullLabel: definition.fullLabel,
      status: graphSyncStatus,
      detail: normalizeText(props.analysis.graphSyncSummary) || defaultIdleDetail(key),
    }
  }

  if (action) {
    return {
      key,
      label: definition.label,
      fullLabel: definition.fullLabel,
      status: 'success',
      detail: normalizeText(action?.result_summary) || '本轮已执行',
    }
  }

  if (requestState === 'running') {
    if (key === 'tool_router') {
      return {
        key,
        label: definition.label,
        fullLabel: definition.fullLabel,
        status: 'running',
        detail: '正在判定当前会话请求的处理路径',
      }
    }
    return {
      key,
      label: definition.label,
      fullLabel: definition.fullLabel,
      status: 'pending',
      detail: '等待上游节点结果',
    }
  }

  if (requestState === 'error') {
    if (key === 'tool_router') {
      return {
        key,
        label: definition.label,
        fullLabel: definition.fullLabel,
        status: 'error',
        detail: '主请求失败，未完整返回执行结果',
      }
    }
    return {
      key,
      label: definition.label,
      fullLabel: definition.fullLabel,
      status: 'idle',
      detail: defaultIdleDetail(key),
    }
  }

  if (requestState === 'success') {
    return {
      key,
      label: definition.label,
      fullLabel: definition.fullLabel,
      status: shouldSkipForRoute(key, routeIntent) ? 'skipped' : 'idle',
      detail: skippedOrIdleDetail(key, routeIntent),
    }
  }

  return {
    key,
    label: definition.label,
    fullLabel: definition.fullLabel,
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
    return '当前请求路由下未触发该模块'
  }
  return defaultIdleDetail(key)
}

function defaultIdleDetail(key) {
  if (key === 'dossier_intelligence') {
    return '案卷级整理不属于当前会话主链路'
  }
  if (key === 'summary_update') {
    return '本轮未触发摘要更新'
  }
  if (key === 'participant_resolution') {
    return '本轮未触发主体确认写入'
  }
  if (key === 'graph_sync') {
    return '当前未触发图谱同步状态更新'
  }
  return '等待当前会话请求触发'
}

function showExecutionDetail(entry) {
  const status = normalizeKey(entry?.status)
  return ['success', 'running', 'warning', 'error', 'pending'].includes(status)
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
  gap: 14px;
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
  border-radius: 16px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.96) 0%, rgba(248, 250, 252, 0.96) 100%);
  border: 1px solid rgba(148, 163, 184, 0.18);
  box-shadow: 0 10px 28px rgba(15, 23, 42, 0.04);
  padding: 14px;
}

.panel-section-compact {
  padding-bottom: 12px;
}

.graph-first {
  background:
    radial-gradient(circle at top right, rgba(99, 102, 241, 0.12), transparent 30%),
    linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
}

.section-title,
.subsection-title {
  font-size: 13px;
  font-weight: 700;
  color: #1f2937;
  margin-bottom: 10px;
}

.section-title-tight {
  margin-bottom: 4px;
}

.section-title-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 12px;
}

.section-summary {
  font-size: 11px;
  color: #64748b;
  white-space: nowrap;
}

.section-description {
  font-size: 11px;
  line-height: 1.5;
  color: #94a3b8;
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
.timeline-list,
.component-note-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.component-status-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.component-status-row {
  min-width: 0;
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid rgba(148, 163, 184, 0.14);
  background: rgba(248, 250, 252, 0.82);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.component-status-copy {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.component-status-copy strong {
  font-size: 12px;
  font-weight: 700;
  color: #0f172a;
}

.component-status-copy span {
  font-size: 11px;
  color: #64748b;
  line-height: 1.5;
}

.component-status-meta {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: #cbd5e1;
}

.status-dot.status-success {
  background: #22c55e;
}

.status-dot.status-running {
  background: #f59e0b;
}

.status-dot.status-pending,
.status-dot.status-skipped {
  background: #93c5fd;
}

.status-dot.status-warning,
.status-dot.status-error {
  background: #f97316;
}

.component-status-badge {
  font-size: 11px;
  color: #64748b;
  white-space: nowrap;
}

.component-secondary-group {
  margin-top: 12px;
}

.compact-subsection-title {
  margin-bottom: 8px;
  font-size: 11px;
  font-weight: 700;
  color: #64748b;
}

.component-inline-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.component-inline-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-radius: 999px;
  border: 1px solid rgba(148, 163, 184, 0.14);
  background: rgba(248, 250, 252, 0.75);
  font-size: 11px;
  color: #475569;
}

.component-inline-chip span:first-child {
  color: #0f172a;
  font-weight: 600;
}

.component-note {
  padding: 9px 11px;
  border-radius: 12px;
  background: rgba(248, 250, 252, 0.72);
  border: 1px solid rgba(148, 163, 184, 0.14);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.component-note strong {
  font-size: 12px;
  color: #0f172a;
}

.component-note span {
  font-size: 11px;
  color: #64748b;
  line-height: 1.5;
}

.timeline-item {
  padding: 10px 12px;
  background: rgba(248, 250, 252, 0.75);
  border-radius: 12px;
  border: 1px solid rgba(148, 163, 184, 0.14);
}

.timeline-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}

.timeline-header strong {
  font-size: 12px;
  color: #0f172a;
}

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
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(248, 250, 252, 0.72);
  border: 1px solid rgba(148, 163, 184, 0.14);
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
  background: rgba(255, 247, 237, 0.92);
  border: 1px solid rgba(251, 146, 60, 0.34);
  color: #9a3412;
  font-size: 12px;
  line-height: 1.5;
}

.analysis-empty-state {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  border-radius: 14px;
  background: rgba(248, 250, 252, 0.72);
  border: 1px dashed rgba(148, 163, 184, 0.2);
}

.analysis-empty-copy {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.analysis-empty-copy strong {
  font-size: 13px;
  color: #0f172a;
  line-height: 1.6;
}

.analysis-empty-copy span {
  font-size: 12px;
  color: #64748b;
  line-height: 1.6;
}

.analysis-empty-hints {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.analysis-empty-hint {
  display: inline-flex;
  align-items: center;
  padding: 6px 10px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.75);
  border: 1px solid rgba(148, 163, 184, 0.14);
  font-size: 11px;
  color: #475569;
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

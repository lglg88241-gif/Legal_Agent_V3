<template>
  <div :class="['analysis-panel', { collapsed }]">
    <div class="panel-toolbar">
      <div v-if="!collapsed" class="toolbar-title">
        <span class="eyebrow">Neo4j Live</span>
        <span class="headline">知识图谱侧边栏</span>
      </div>
      <el-tooltip :content="collapsed ? '展开侧边栏' : '收起侧边栏'" placement="left">
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
            <div class="section-title section-title-tight">执行状态</div>
            <div class="section-description">展示当前请求真实触发的关键模块和执行状态。</div>
          </div>
          <span class="section-summary">{{ componentStatusSummary }}</span>
        </div>

        <div class="component-status-list">
          <div
            v-for="entry in displayedExecutionEntries"
            :key="entry.key"
            :class="['component-status-row', `status-${normalizeKey(entry.status)}`]"
          >
            <div class="component-status-copy">
              <strong>{{ entry.label }}</strong>
              <span>
                {{ showExecutionDetail(entry) ? entry.detail : displayExecutionStatusQuietLabel(entry.status) }}
              </span>
              <span v-if="hasExecutionTrace && entry.meta" class="component-trace-meta-line">
                {{ entry.meta }}
              </span>
            </div>
            <div class="component-status-meta">
              <span :class="['status-dot', `status-${normalizeKey(entry.status)}`]"></span>
              <span class="component-status-badge">{{ displayExecutionStatusQuietLabel(entry.status) }}</span>
            </div>
          </div>
        </div>

        <div v-if="hasExecutionTrace && executionTraceLegacyFallback.used" class="analysis-warning">
          {{ `传统流程兜底：${executionTraceLegacyFallback.reason || '未提供原因'}。这不代表失败，只表示回退到旧链路。` }}
        </div>

        <details v-if="hasExecutionTrace && executionTraceTriggeredGuards.length" class="trace-guard-details">
          <summary>{{ `执行保护条件（${executionTraceTriggeredGuards.length}）` }}</summary>
          <div class="trace-guard-list">
            <div
              v-for="guard in executionTraceTriggeredGuards"
              :key="guard.name"
              class="trace-guard-item"
            >
              <span>{{ guard.label }}</span>
              <el-tag size="small" type="warning" effect="plain">已生效</el-tag>
            </div>
          </div>
        </details>

        <div v-if="!hasExecutionTrace && secondaryExecutionEntries.length" class="component-secondary-group">
          <div class="compact-subsection-title">更多组件状态</div>
          <div class="component-inline-list">
            <div
              v-for="entry in secondaryExecutionEntries"
              :key="`secondary-${entry.key}`"
              :class="['component-inline-chip', `status-${normalizeKey(entry.status)}`]"
            >
              <span>{{ entry.label }}</span>
              <span>{{ displayExecutionStatusQuietLabel(entry.status) }}</span>
            </div>
          </div>
        </div>

        <div v-if="!hasExecutionTrace && highlightedExecutionEntries.length" class="component-note-list">
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
            <div class="section-description">展示当前会话请求的处理过程和结果摘要。</div>
          </div>
        </div>

        <div v-if="!hasAnalysisContent" class="analysis-empty-state">
          <div class="analysis-empty-copy">
            <strong>发送问题后，我会在这里展示本轮分析过程。</strong>
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
                <el-tag :type="displayExecutionStatusTagType(item.status)" size="small" effect="plain">
                  {{ displayExecutionStatusLabel(item.status) }}
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
      executionTrace: null,
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

const EXECUTION_COMPONENT_LABELS = {
  request: '请求处理',
  tool_router: '任务分流',
  fact_updater: '案情理解',
  retrieval_tools: '依据检索',
  reflector: '风险审查',
  legal_advisor: '法律回答生成',
  greeting: '问候响应',
  out_of_scope: '范围拦截',
  graph_sync: '图谱更新',
  dossier_intelligence: '案卷智能分析',
  summary_update: '摘要更新',
  participant_resolution: '主体关系确认',
  message_understanding: '消息理解',
  message_understanding_shadow: '消息理解影子链路',
  message_orchestrator: '执行计划',
  dossier_memory: '会话/案卷指代解析',
  legal_agent_answer: '法律问答',
  case_update_acknowledgement: '事实补充确认',
  fact_confirmation: '上一事实确认',
}

const TRACE_STEP_LABELS = {
  message_understanding: '消息理解',
  message_understanding_agent: '消息理解智能体',
  execution_plan: '执行计划',
  message_orchestrator: '执行计划',
  dossier_memory_subject_reference: '会话/案卷指代解析',
  assistant_acknowledgement: '事实补充确认',
  legal_agent_answer: '法律问答',
  fact_confirmation: '上一事实确认',
  fact_persistence: '案情结构化记录',
  graph_update: '图谱更新',
  dossier_intelligence: '案卷智能分析',
  participant_resolution: '主体关系确认',
  summary_update: '案卷摘要更新',
  legacy_fallback: '传统流程兜底',
}

const ROUTE_LABELS = {
  legal_qa: '法律问答',
  case_update: '案情更新',
  dossier_memory: '案卷/会话记忆查询',
  general_consult: '一般咨询',
  document_assist: '文书辅助',
  greeting: '问候',
  out_of_scope: '超出范围',
  unknown: '未知分流',
}

const RESPONSE_MODE_LABELS = {
  acknowledge_and_persist: '确认并持久化',
  case_analysis_without_persistence: '仅分析不持久化',
  legal_answer: '法律回答',
  dossier_memory_answer: '记忆解析回答',
}

const STATUS_LABELS = {
  success: '已完成',
  skipped: '已跳过',
  failed: '失败',
  fallback: '兜底',
  scheduled: '已调度',
  running: '执行中',
  pending: '待执行',
  idle: '空闲',
  warning: '需关注',
  error: '失败',
}

const GUARD_LABELS = {
  temporary_blocks_persistence: 'temporary 模式阻断结构化持久化',
  legal_qa_blocks_graph: 'LEGAL_QA 阻断图谱更新',
  missing_previous_fact: '未找到可确认的上一条事实',
  dossier_followup_question_blocks_persistence: '案卷追问阻断案情写入',
  non_persistable_case_update: '当前 CASE_UPDATE 不满足持久化条件',
}

const executionDefinitions = [
  { key: 'tool_router', label: '任务分流', fullLabel: '任务分流' },
  { key: 'fact_updater', label: '案情理解', fullLabel: '案情理解' },
  { key: 'retrieval_tools', label: '依据检索', fullLabel: '依据检索' },
  { key: 'reflector', label: '风险审查', fullLabel: '风险审查' },
  { key: 'legal_advisor', label: '法律回答生成', fullLabel: '法律回答生成' },
  { key: 'graph_sync', label: '图谱更新', fullLabel: '图谱更新' },
  { key: 'dossier_intelligence', label: '案卷智能分析', fullLabel: '案卷智能分析' },
  { key: 'summary_update', label: '摘要更新', fullLabel: '摘要更新' },
  { key: 'participant_resolution', label: '主体关系确认', fullLabel: '主体关系确认' },
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
const executionTrace = computed(() =>
  props.analysis.executionTrace && typeof props.analysis.executionTrace === 'object'
    ? props.analysis.executionTrace
    : null
)
const hasExecutionTrace = computed(() => Boolean(executionTrace.value))

const requestStateLabel = computed(() => displayExecutionStatusLabel(props.analysis.requestState || 'idle'))
const routeIntentLabel = computed(() => routeLabel(props.analysis.routeIntent))

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

const executionTraceLegacyFallback = computed(() => {
  const fallback = executionTrace.value?.legacy_fallback
  if (!fallback || typeof fallback !== 'object') {
    return { used: false, reason: '' }
  }
  return {
    used: Boolean(fallback.used),
    reason: normalizeText(fallback.reason),
  }
})

const executionTraceTriggeredGuards = computed(() =>
  (Array.isArray(executionTrace.value?.guards_applied) ? executionTrace.value.guards_applied : [])
    .filter((guard) => guard && typeof guard === 'object' && guard.triggered)
    .map((guard) => ({
      name: normalizeText(guard.name),
      label: guardLabel(guard.name),
    }))
)

const executionTraceEntries = computed(() => {
  if (!hasExecutionTrace.value) return []
  const traceValue = executionTrace.value || {}
  const entries = []

  const messageUnderstanding = traceValue.message_understanding || {}
  if (messageUnderstanding && typeof messageUnderstanding === 'object' && Object.keys(messageUnderstanding).length) {
    entries.push({
      key: 'trace-message-understanding',
      label: '消息理解',
      fullLabel: '消息理解',
      status: messageUnderstanding.fallback_used ? 'fallback' : 'success',
      detail: `${intentLabel(messageUnderstanding.intent)} -> ${routeLabel(messageUnderstanding.route_hint)}`,
      meta: formatTraceMeta({
        agent: 'message_understanding_agent',
        provider: messageUnderstanding.provider,
        model: messageUnderstanding.model,
        latencyMs: messageUnderstanding.latency_ms,
      }),
    })
  }

  const executionPlanValue = traceValue.execution_plan || {}
  if (executionPlanValue && typeof executionPlanValue === 'object' && Object.keys(executionPlanValue).length) {
    const stepNames = Array.isArray(executionPlanValue.step_names) ? executionPlanValue.step_names : []
    const mappedSteps = stepNames.map((name) => executionTraceLabel(name))
    const responseModeText = normalizeText(executionPlanValue.response_mode)
      ? ` · ${responseModeLabel(executionPlanValue.response_mode)}`
      : ''
    entries.push({
      key: 'trace-execution-plan',
      label: '执行计划',
      fullLabel: '执行计划',
      status: executionPlanValue.actionable ? 'success' : 'fallback',
      detail: `${routeLabel(executionPlanValue.primary_route)}${responseModeText} · ${mappedSteps.join('、') || '传统流程兜底'}`,
      meta: formatTraceMeta({ agent: 'message_orchestrator' }),
    })
  }

  for (const step of Array.isArray(traceValue.steps) ? traceValue.steps : []) {
    if (!step || typeof step !== 'object') continue
    entries.push({
      key: `trace-step-${normalizeText(step.name)}-${entries.length}`,
      label: executionTraceLabel(step.name),
      fullLabel: executionTraceLabel(step.name),
      status: normalizeKey(step.status) || 'success',
      detail: buildTraceStepDetail(step),
      meta: formatTraceMeta({
        agent: step.agent,
        provider: step.provider,
        model: step.model,
        latencyMs: step.latency_ms,
      }),
    })
  }

  return entries
})

const displayedExecutionEntries = computed(() =>
  hasExecutionTrace.value ? executionTraceEntries.value : coreExecutionEntries.value
)

const componentStatusSummary = computed(() => {
  const entries = hasExecutionTrace.value ? executionTraceEntries.value : executionComponentEntries.value
  const running = entries.filter((entry) => normalizeKey(entry.status) === 'running').length
  const warning = entries.filter((entry) =>
    ['warning', 'error', 'failed', 'fallback'].includes(normalizeKey(entry.status))
  ).length
  const success = entries.filter((entry) => normalizeKey(entry.status) === 'success').length

  if (running > 0) return `${running} 个步骤执行中`
  if (warning > 0) return `${warning} 个步骤需要关注`
  if (success > 0) return `${success} 个步骤已完成`
  return '等待请求触发'
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
  if (hasExecutionTrace.value) {
    return executionTraceEntries.value.map((entry, index) => ({
      key: `${entry.key}-${index}`,
      label: entry.label,
      status: entry.status,
      summary: entry.meta ? `${entry.detail} · ${entry.meta}` : entry.detail,
    }))
  }

  const items = []
  for (const action of props.analysis.actions || []) {
    const key = normalizeKey(action?.tool)
    if (!key) continue
    items.push({
      key,
      label: executionLabelForTool(key),
      status: normalizeKey(action?.status) || 'success',
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
      summary: '当前请求处理中，等待后端返回执行结果。',
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

function unknownLabel(prefix, value, emptyFallback) {
  const raw = normalizeText(value)
  if (!raw) return emptyFallback
  return `${prefix}：${raw}`
}

function componentLabel(key) {
  const labels = {
    chroma: 'Chroma',
    neo4j: 'Neo4j',
    llm: 'LLM',
    celery: 'Celery',
  }
  return labels[key] || unknownLabel('未知组件', key, '未知组件')
}

function intentLabel(value) {
  const labels = {
    subject_reference: '主体指代',
    fact_update: '事实补充',
    confirm_previous_fact: '上一事实确认',
    legal_question: '法律问题',
    advice_question: '维权建议',
    document_request: '文书请求',
    unknown: '未知意图',
  }
  return labels[normalizeKey(value)] || unknownLabel('未知意图', value, '未知意图')
}

function executionLabelForTool(key) {
  return EXECUTION_COMPONENT_LABELS[normalizeKey(key)] || unknownLabel('未知组件', key, '未知组件')
}

function executionTraceLabel(name) {
  return TRACE_STEP_LABELS[normalizeKey(name)] || unknownLabel('未知步骤', name, '未知步骤')
}

function routeLabel(value) {
  return ROUTE_LABELS[normalizeKey(value)] || unknownLabel('未知分流', value, '未返回')
}

function responseModeLabel(value) {
  return RESPONSE_MODE_LABELS[normalizeKey(value)] || unknownLabel('未知响应模式', value, '未声明')
}

function guardLabel(name) {
  return GUARD_LABELS[normalizeKey(name)] || unknownLabel('未知保护', name, '未知保护')
}

function formatTraceMeta({ agent, provider, model, latencyMs }) {
  const parts = []
  const normalizedAgent = normalizeText(agent)
  const normalizedProvider = normalizeText(provider)
  const normalizedModel = normalizeText(model)
  const latency = Number(latencyMs)

  if (normalizedAgent) parts.push(normalizedAgent)
  if (normalizedProvider || normalizedModel) {
    parts.push([normalizedProvider, normalizedModel].filter(Boolean).join(' / '))
  }
  if (Number.isFinite(latency) && latency >= 0) {
    parts.push(`${Math.round(latency)} ms`)
  }
  return parts.join(' · ')
}

function buildTraceStepDetail(step) {
  const details = step?.details && typeof step.details === 'object' ? step.details : {}
  const status = normalizeKey(step?.status)
  if (status === 'skipped') {
    return unknownLabel('跳过原因', step?.skip_reason, '已跳过')
  }
  if (status === 'fallback') {
    return unknownLabel('兜底原因', step?.fallback_reason, '已兜底')
  }
  if (status === 'failed') {
    return normalizeText(details.error) || '执行失败'
  }
  if (normalizeText(details.selected_scope)) {
    return `已选择作用域：${details.selected_scope}`
  }
  if (normalizeText(details.task_id)) {
    return `已调度任务：${details.task_id}`
  }
  if (normalizeText(details.graph_update_source)) {
    return `图谱更新来源：${details.graph_update_source}`
  }
  if (normalizeText(details.input_text)) {
    return '已写入结构化案情'
  }
  return '本步骤已完成'
}

function displayExecutionStatusLabel(status) {
  return STATUS_LABELS[normalizeKey(status)] || unknownLabel('未知状态', status, '未知状态')
}

function displayExecutionStatusQuietLabel(status) {
  return STATUS_LABELS[normalizeKey(status)] || unknownLabel('未知状态', status, '未知状态')
}

function displayExecutionStatusTagType(status) {
  switch (normalizeKey(status)) {
    case 'success':
      return 'success'
    case 'running':
      return 'warning'
    case 'warning':
      return 'warning'
    case 'error':
    case 'failed':
      return 'danger'
    case 'pending':
    case 'scheduled':
    case 'skipped':
      return 'info'
    case 'fallback':
      return 'warning'
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
      status: normalizeKey(action?.status) || 'success',
      detail: normalizeText(action?.result_summary) || '本轮已完成执行',
    }
  }

  if (requestState === 'running') {
    if (key === 'tool_router') {
      return {
        key,
        label: definition.label,
        fullLabel: definition.fullLabel,
        status: 'running',
        detail: '正在判断当前请求的处理路径。',
      }
    }
    return {
      key,
      label: definition.label,
      fullLabel: definition.fullLabel,
      status: 'pending',
      detail: '等待上游步骤完成。',
    }
  }

  if (requestState === 'error') {
    if (key === 'tool_router') {
      return {
        key,
        label: definition.label,
        fullLabel: definition.fullLabel,
        status: 'failed',
        detail: '主请求失败，未完整返回执行结果。',
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
    return ['dossier_intelligence', 'summary_update', 'participant_resolution'].includes(key)
  }
  return false
}

function skippedOrIdleDetail(key, routeIntent) {
  if (shouldSkipForRoute(key, routeIntent)) {
    return '当前请求路径下未触发该组件。'
  }
  return defaultIdleDetail(key)
}

function defaultIdleDetail(key) {
  if (key === 'dossier_intelligence') {
    return '案卷级智能分析不属于当前会话主链路。'
  }
  if (key === 'summary_update') {
    return '本轮未触发摘要更新。'
  }
  if (key === 'participant_resolution') {
    return '本轮未触发主体确认写入。'
  }
  if (key === 'graph_sync') {
    return '当前未触发图谱同步状态更新。'
  }
  return '等待当前请求触发。'
}

function showExecutionDetail(entry) {
  const status = normalizeKey(entry?.status)
  return ['success', 'running', 'warning', 'error', 'failed', 'pending', 'fallback', 'scheduled'].includes(status)
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

.component-trace-meta-line {
  color: #94a3b8;
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

.status-dot.status-fallback {
  background: #f59e0b;
}

.status-dot.status-failed {
  background: #ef4444;
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

.trace-guard-details {
  margin-top: 10px;
  border: 1px solid rgba(148, 163, 184, 0.16);
  border-radius: 12px;
  background: rgba(248, 250, 252, 0.72);
  padding: 10px 12px;
}

.trace-guard-details summary {
  cursor: pointer;
  font-size: 12px;
  font-weight: 600;
  color: #334155;
}

.trace-guard-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 10px;
}

.trace-guard-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 11px;
  color: #475569;
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

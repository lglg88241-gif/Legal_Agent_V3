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
      </div>

      <div class="panel-section">
        <div class="section-title-row">
          <div>
            <div class="section-title section-title-tight">Agent 执行过程</div>
            <div class="section-description">展示本轮计划、工具调用、保护规则、写入动作和最终依据。</div>
          </div>
          <span class="section-summary">{{ requestStateLabel }}</span>
        </div>

        <div class="meta-grid">
          <div class="meta-card">
            <span>分流</span>
            <strong>{{ routeIntentLabel }}</strong>
          </div>
          <div class="meta-card">
            <span>耗时</span>
            <strong>{{ latencyLabel }}</strong>
          </div>
          <div class="meta-card">
            <span>置信度</span>
            <strong>{{ confidenceLabel }}</strong>
          </div>
          <div class="meta-card">
            <span>执行来源</span>
            <strong>{{ hasExecutionTrace ? 'execution_trace' : 'agent_actions fallback' }}</strong>
          </div>
        </div>

        <div v-if="analysis.requestError" class="analysis-warning">
          {{ analysis.requestError }}
        </div>

        <template v-if="hasExecutionTrace">
          <div class="process-block">
            <div class="subsection-title">思考摘要</div>
            <div class="summary-card">
              <strong>{{ agentPlanRouteLabel }}</strong>
              <span>{{ agentPlanSummary }}</span>
              <small v-if="agentPlanMeta">{{ agentPlanMeta }}</small>
            </div>
          </div>

          <div v-if="toolEntries.length" class="process-block">
            <div class="subsection-title">工具调用</div>
            <div class="stack-list">
              <div v-for="tool in toolEntries" :key="tool.key" class="stack-item">
                <div class="stack-header">
                  <strong>{{ tool.label }}</strong>
                  <el-tag :type="statusTagType(tool.status)" size="small" effect="plain">
                    {{ statusLabel(tool.status) }}
                  </el-tag>
                </div>
                <div class="stack-body">{{ tool.summary }}</div>
                <div v-if="tool.meta" class="stack-meta">{{ tool.meta }}</div>
              </div>
            </div>
          </div>

          <div v-if="triggeredGuards.length" class="process-block">
            <div class="subsection-title">保护规则</div>
            <div class="tag-list">
              <el-tag
                v-for="guard in triggeredGuards"
                :key="guard.name"
                size="small"
                effect="plain"
                type="warning"
              >
                {{ guard.label }}
              </el-tag>
            </div>
          </div>

          <div v-if="writeEntries.length" class="process-block">
            <div class="subsection-title">写入动作</div>
            <div class="stack-list">
              <div v-for="write in writeEntries" :key="write.key" class="stack-item">
                <div class="stack-header">
                  <strong>{{ write.label }}</strong>
                  <el-tag :type="statusTagType(write.status)" size="small" effect="plain">
                    {{ statusLabel(write.status) }}
                  </el-tag>
                </div>
                <div class="stack-body">{{ write.summary }}</div>
              </div>
            </div>
          </div>

          <div v-if="finalBasis.summary" class="process-block">
            <div class="subsection-title">最终依据</div>
            <div class="summary-card">
              <span>{{ finalBasis.summary }}</span>
              <small v-if="finalBasis.source">来源：{{ finalBasis.source }}</small>
              <div v-if="finalBasis.evidence.length" class="evidence-list">
                <span v-for="item in finalBasis.evidence" :key="item" class="evidence-chip">{{ item }}</span>
              </div>
            </div>
          </div>

          <div v-if="legacyFallback.used" class="analysis-warning">
            传统流程兜底：{{ legacyFallback.reason || '未提供原因' }}。不代表失败，只表示回退到旧链路。
          </div>

          <div v-if="stepEntries.length" class="process-block">
            <div class="subsection-title">执行步骤</div>
            <div class="stack-list">
              <div v-for="step in stepEntries" :key="step.key" class="stack-item">
                <div class="stack-header">
                  <strong>{{ step.label }}</strong>
                  <el-tag :type="statusTagType(step.status)" size="small" effect="plain">
                    {{ statusLabel(step.status) }}
                  </el-tag>
                </div>
                <div class="stack-body">{{ step.summary }}</div>
                <div v-if="step.meta" class="stack-meta">{{ step.meta }}</div>
              </div>
            </div>
          </div>
        </template>

        <template v-else>
          <div class="process-block">
            <div class="subsection-title">执行摘要</div>
            <div v-if="fallbackActionEntries.length" class="stack-list">
              <div v-for="item in fallbackActionEntries" :key="item.key" class="stack-item">
                <div class="stack-header">
                  <strong>{{ item.label }}</strong>
                  <el-tag :type="statusTagType(item.status)" size="small" effect="plain">
                    {{ statusLabel(item.status) }}
                  </el-tag>
                </div>
                <div class="stack-body">{{ item.summary }}</div>
              </div>
            </div>
            <div v-else class="empty-state">
              发送问题后，这里会展示本轮 Agent 的执行过程。
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

const STEP_LABELS = {
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

const TOOL_LABELS = {
  exact_law_lookup: '精确法条查询',
  legal_rag_search: '法律检索',
  case_search: '案例检索',
  case_fact_extract: '案情事实抽取',
  case_summary_update: '案情摘要更新',
  conversation_graph_extract: '图谱抽取',
  schedule_graph_update: '图谱更新调度',
  current_conversation_graph_query: '当前会话图谱查询',
  dossier_graph_context_query: '案卷图谱查询',
  display_graph_build: 'Display Graph 构建',
  dossier_intelligence_build: '案卷智能分析',
  subject_reference_resolve: '主体指代解析',
  participant_resolution_apply: '主体确认写入',
  party_naming_confirm: '主体命名确认',
  legal_answer_compose: '法律回答生成',
  memory_answer_compose: '记忆回答生成',
  acknowledgement_compose: '轻量确认生成',
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
  memory_answer: '记忆解析回答',
  clarification: '澄清',
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
  planned: '已规划',
  blocked: '已阻断',
  applied: '已写入',
}

const GUARD_LABELS = {
  temporary_blocks_persistence: 'temporary 阻断持久化与图谱写入',
  legal_qa_blocks_graph: 'LEGAL_QA 阻断图谱污染',
  missing_previous_fact: '缺少可确认的上一条事实',
  dossier_followup_question_blocks_persistence: '案卷追问阻断案情写入',
  non_persistable_case_update: '当前 CASE_UPDATE 不满足写入条件',
}

const executionTrace = computed(() =>
  props.analysis.executionTrace && typeof props.analysis.executionTrace === 'object'
    ? props.analysis.executionTrace
    : null,
)

const hasExecutionTrace = computed(() => Boolean(executionTrace.value))

const requestStateLabel = computed(() => statusLabel(props.analysis.requestState || 'idle'))
const routeIntentLabel = computed(() => routeLabel(props.analysis.routeIntent))

const latencyLabel = computed(() => {
  const latency = Number(props.analysis.latencyMs)
  return Number.isFinite(latency) && latency >= 0 ? `${Math.round(latency)} ms` : '暂无'
})

const confidenceLabel = computed(() => {
  const confidence = Number(props.analysis.confidenceScore)
  return Number.isFinite(confidence) && confidence > 0 ? confidence.toFixed(2) : '暂无'
})

const agentPlan = computed(() =>
  executionTrace.value?.agent_plan && typeof executionTrace.value.agent_plan === 'object'
    ? executionTrace.value.agent_plan
    : {},
)

const agentPlanSummary = computed(() =>
  normalizeText(agentPlan.value.plan_summary) || '本轮未提供计划摘要。',
)

const agentPlanRouteLabel = computed(() => {
  const route = routeLabel(agentPlan.value.route || executionTrace.value?.execution_plan?.primary_route || props.analysis.routeIntent)
  const mode = responseModeLabel(agentPlan.value.response_style)
  return mode ? `${route} / ${mode}` : route
})

const agentPlanMeta = computed(() => {
  const specialist = normalizeText(agentPlan.value.specialist)
  const tools = Array.isArray(agentPlan.value.tool_names) ? agentPlan.value.tool_names.length : 0
  const parts = []
  if (specialist) parts.push(`specialist: ${specialist}`)
  if (tools) parts.push(`tools: ${tools}`)
  return parts.join(' · ')
})

const toolEntries = computed(() =>
  (Array.isArray(executionTrace.value?.tool_calls) ? executionTrace.value.tool_calls : []).map((tool, index) => ({
    key: `${normalizeKey(tool.name)}-${index}`,
    label: toolLabel(tool.name),
    status: normalizeKey(tool.status) || 'planned',
    summary:
      normalizeText(tool.result_summary) ||
      normalizeText(tool.arguments_summary) ||
      '已登记工具调用，但未返回额外摘要。',
    meta: [normalizeText(tool.category), normalizeText(tool.dispatch_mode), normalizeText(tool.current_implementation)]
      .filter(Boolean)
      .join(' · '),
  })),
)

const triggeredGuards = computed(() =>
  (Array.isArray(executionTrace.value?.guards_applied) ? executionTrace.value.guards_applied : [])
    .filter((guard) => guard && typeof guard === 'object' && guard.triggered)
    .map((guard) => ({
      name: normalizeText(guard.name),
      label: guardLabel(guard.name),
    })),
)

const writeEntries = computed(() => {
  const requested = Array.isArray(executionTrace.value?.writes?.requested) ? executionTrace.value.writes.requested : []
  const applied = Array.isArray(executionTrace.value?.writes?.applied) ? executionTrace.value.writes.applied : []
  const appliedByName = new Map(applied.map((item) => [normalizeKey(item.name), item]))

  return requested.map((item, index) => {
    const key = normalizeKey(item.name)
    const appliedItem = appliedByName.get(key)
    let status = 'planned'
    let summary = normalizeText(item.reason) || '已请求写入。'

    if (appliedItem) {
      status = 'applied'
      summary = normalizeText(appliedItem.input_text)
        ? `已写入：${normalizeText(appliedItem.input_text)}`
        : '写入已执行。'
    } else if (item.allowed === false) {
      status = 'blocked'
      const blockedBy = Array.isArray(item.blocked_by) ? item.blocked_by.map(guardLabel).join('、') : ''
      summary = blockedBy ? `已阻断：${blockedBy}` : '写入被阻断。'
    }

    return {
      key: `${key}-${index}`,
      label: writeLabel(item.name),
      status,
      summary,
    }
  })
})

const finalBasis = computed(() => {
  const payload = executionTrace.value?.final_basis
  if (!payload || typeof payload !== 'object') {
    return { summary: '', source: '', evidence: [] }
  }
  return {
    summary: normalizeText(payload.summary),
    source: normalizeText(payload.source),
    evidence: Array.isArray(payload.evidence)
      ? payload.evidence.map((item) => normalizeText(item)).filter(Boolean)
      : [],
  }
})

const legacyFallback = computed(() => {
  const payload = executionTrace.value?.legacy_fallback
  if (!payload || typeof payload !== 'object') {
    return { used: false, reason: '' }
  }
  return {
    used: Boolean(payload.used),
    reason: normalizeText(payload.reason),
  }
})

const stepEntries = computed(() =>
  (Array.isArray(executionTrace.value?.steps) ? executionTrace.value.steps : []).map((step, index) => ({
    key: `${normalizeKey(step.name)}-${index}`,
    label: stepLabel(step.name),
    status: normalizeKey(step.status) || 'success',
    summary: buildStepSummary(step),
    meta: buildStepMeta(step),
  })),
)

const fallbackActionEntries = computed(() =>
  (props.analysis.actions || []).map((action, index) => ({
    key: `${normalizeKey(action?.tool)}-${index}`,
    label: toolLabel(action?.tool) || unknownLabel('未知工具', action?.tool, '未知工具'),
    status: normalizeKey(action?.status) || 'success',
    summary: normalizeText(action?.result_summary) || '本轮已完成执行。',
  })),
)

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

function stepLabel(name) {
  return STEP_LABELS[normalizeKey(name)] || unknownLabel('未知步骤', name, '未知步骤')
}

function toolLabel(name) {
  return TOOL_LABELS[normalizeKey(name)] || unknownLabel('未知工具', name, '未知工具')
}

function routeLabel(value) {
  return ROUTE_LABELS[normalizeKey(value)] || unknownLabel('未知分流', value, '未知分流')
}

function responseModeLabel(value) {
  if (!normalizeText(value)) return ''
  return RESPONSE_MODE_LABELS[normalizeKey(value)] || unknownLabel('未知响应模式', value, '未知响应模式')
}

function statusLabel(value) {
  return STATUS_LABELS[normalizeKey(value)] || unknownLabel('未知状态', value, '未知状态')
}

function guardLabel(value) {
  return GUARD_LABELS[normalizeKey(value)] || unknownLabel('未知保护规则', value, '未知保护规则')
}

function writeLabel(value) {
  const mapping = {
    structured_case_persistence: '结构化案情写入',
    graph_update_schedule: '图谱更新调度',
  }
  return mapping[normalizeKey(value)] || unknownLabel('未知写入动作', value, '未知写入动作')
}

function statusTagType(status) {
  switch (normalizeKey(status)) {
    case 'success':
    case 'applied':
      return 'success'
    case 'running':
    case 'fallback':
      return 'warning'
    case 'failed':
    case 'error':
      return 'danger'
    case 'blocked':
      return 'warning'
    case 'skipped':
    case 'pending':
    case 'planned':
    case 'scheduled':
      return 'info'
    default:
      return ''
  }
}

function buildStepSummary(step) {
  const details = step?.details && typeof step.details === 'object' ? step.details : {}
  const status = normalizeKey(step?.status)
  if (status === 'skipped') {
    return unknownLabel('跳过原因', step?.skip_reason, '该步骤已跳过。')
  }
  if (status === 'fallback') {
    return unknownLabel('兜底原因', step?.fallback_reason, '该步骤已兜底。')
  }
  if (status === 'failed') {
    return normalizeText(details.error) || '该步骤执行失败。'
  }
  if (normalizeText(details.selected_scope)) {
    return `已选择作用域：${normalizeText(details.selected_scope)}`
  }
  if (normalizeText(details.task_id)) {
    return `已调度任务：${normalizeText(details.task_id)}`
  }
  if (normalizeText(details.input_text)) {
    return `写入输入：${normalizeText(details.input_text)}`
  }
  if (normalizeText(details.graph_update_source)) {
    return `图谱更新来源：${normalizeText(details.graph_update_source)}`
  }
  return '该步骤已完成。'
}

function buildStepMeta(step) {
  const parts = []
  if (normalizeText(step.agent)) parts.push(normalizeText(step.agent))
  if (normalizeText(step.provider) || normalizeText(step.model)) {
    parts.push([normalizeText(step.provider), normalizeText(step.model)].filter(Boolean).join(' / '))
  }
  const latency = Number(step.latency_ms)
  if (Number.isFinite(latency) && latency >= 0) {
    parts.push(`${Math.round(latency)} ms`)
  }
  return parts.join(' · ')
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

.graph-first {
  background:
    radial-gradient(circle at top right, rgba(59, 130, 246, 0.14), transparent 30%),
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

.meta-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}

.meta-card {
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(248, 250, 252, 0.72);
  border: 1px solid rgba(148, 163, 184, 0.14);
}

.meta-card span {
  display: block;
  font-size: 11px;
  color: #64748b;
  margin-bottom: 4px;
}

.meta-card strong {
  font-size: 12px;
  color: #0f172a;
}

.process-block + .process-block {
  margin-top: 14px;
}

.summary-card,
.stack-item,
.empty-state {
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(248, 250, 252, 0.78);
  border: 1px solid rgba(148, 163, 184, 0.14);
}

.summary-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.summary-card strong {
  font-size: 12px;
  color: #0f172a;
}

.summary-card span,
.summary-card small {
  font-size: 12px;
  color: #475569;
  line-height: 1.6;
}

.stack-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.stack-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}

.stack-header strong {
  font-size: 12px;
  color: #0f172a;
}

.stack-body {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.6;
  color: #475569;
}

.stack-meta {
  margin-top: 6px;
  font-size: 11px;
  color: #94a3b8;
}

.tag-list,
.evidence-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.evidence-chip {
  display: inline-flex;
  align-items: center;
  padding: 5px 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.78);
  border: 1px solid rgba(148, 163, 184, 0.16);
  font-size: 11px;
  color: #475569;
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

.empty-state {
  font-size: 12px;
  color: #64748b;
  line-height: 1.6;
}

.rating-row {
  margin-bottom: 8px;
}

.submit-btn {
  margin-top: 8px;
  width: 100%;
}
</style>

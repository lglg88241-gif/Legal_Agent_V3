<template>
  <div :class="['analysis-panel', { collapsed }]">
    <div class="panel-toolbar">
      <div v-if="!collapsed" class="toolbar-title">
        <span class="eyebrow">分析面板</span>
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
      <div class="panel-section graph-section graph-first">
        <div class="section-title">实时子图</div>
        <div v-if="graphSyncSummary" class="graph-sync-tip">{{ graphSyncSummary }}</div>
        <KnowledgeGraphPanel :graph="graph" />
      </div>

      <div class="panel-section primary-section agent-compact-section">
        <div class="agent-compact-header">
          <div>
            <div class="section-title section-title-tight">Agent 执行过程</div>
            <div class="agent-summary-line">{{ compactSummaryLine }}</div>
          </div>
          <el-tag :type="statusTagType(analysis.requestState)" size="small" effect="plain">
            {{ requestStateLabel }}
          </el-tag>
        </div>

        <div v-if="compactMetaLine" class="agent-meta-line">{{ compactMetaLine }}</div>

        <div v-if="analysis.requestError" class="analysis-warning compact-warning">
          {{ analysis.requestError }}
        </div>

        <div class="compact-sources">
          <div class="subsection-title compact-subsection-title">参考来源</div>
          <div v-if="compactSources.length" class="compact-source-list">
            <div v-for="source in compactSources" :key="source" class="compact-source-item">
              {{ source }}
            </div>
          </div>
          <div v-else class="empty-state compact-empty">当前请求尚未返回参考来源。</div>
        </div>

        <el-button
          class="trace-toggle"
          size="small"
          text
          type="primary"
          @click="showTraceDetail = !showTraceDetail"
        >
          {{ showTraceDetail ? '收起详情' : '查看详情' }}
        </el-button>

        <div v-if="showTraceDetail" class="agent-trace-detail">
          <template v-if="hasExecutionTrace">
            <div class="process-block">
              <div class="subsection-title">思考摘要</div>
              <div class="summary-card">
                <strong>{{ agentPlanRouteLabel }}</strong>
                <span>{{ agentPlanSummary }}</span>
                <small v-if="agentPlanMeta">{{ agentPlanMeta }}</small>
              </div>
            </div>

            <div class="process-block">
              <div class="subsection-title">工具调用</div>
              <div v-if="toolEntries.length" class="stack-list">
                <div v-for="tool in toolEntries" :key="tool.key" class="stack-item">
                  <div class="stack-header">
                    <strong>{{ tool.label }}</strong>
                    <el-tag :type="statusTagType(tool.status)" size="small" effect="plain">
                      {{ statusLabel(tool.status) }}
                    </el-tag>
                  </div>
                  <div class="stack-body">{{ tool.summary }}</div>
                  <div v-if="tool.meta" class="stack-meta">{{ tool.meta }}</div>
                  <div v-if="tool.debugMeta" class="stack-debug">{{ tool.debugMeta }}</div>
                </div>
              </div>
              <div v-else class="empty-state">本轮未调用额外工具。</div>
            </div>

            <div class="process-block">
              <div class="subsection-title">保护规则</div>
              <div v-if="guardEntries.length" class="stack-list">
                <div v-for="guard in guardEntries" :key="guard.key" class="stack-item stack-item-warning">
                  <div class="stack-header">
                    <strong>{{ guard.label }}</strong>
                    <el-tag type="warning" size="small" effect="plain">已生效</el-tag>
                  </div>
                  <div v-if="guard.meta" class="stack-debug">{{ guard.meta }}</div>
                </div>
              </div>
              <div v-else class="empty-state">{{ guardEmptyText }}</div>
            </div>

            <div class="process-block">
              <div class="subsection-title">写入动作</div>
              <div v-if="writeEntries.length" class="stack-list">
                <div v-for="write in writeEntries" :key="write.key" class="stack-item">
                  <div class="stack-header">
                    <strong>{{ write.label }}</strong>
                    <el-tag :type="statusTagType(write.status)" size="small" effect="plain">
                      {{ statusLabel(write.status) }}
                    </el-tag>
                  </div>
                  <div class="stack-body">{{ write.summary }}</div>
                  <div v-if="write.meta" class="stack-meta">{{ write.meta }}</div>
                </div>
              </div>
              <div v-else class="empty-state">{{ writeEmptyText }}</div>
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
              本轮已回退到传统流程处理。{{ legacyFallback.reason || '未提供具体原因。' }}
            </div>

            <details v-if="showTechnicalDetails" class="debug-details">
              <summary>技术信息</summary>
              <div class="debug-panel">
                <div v-if="requestIdLabel" class="debug-line">
                  <span>请求标识</span>
                  <strong>{{ requestIdLabel }}</strong>
                </div>
                <div v-if="rawPlanSummary" class="debug-line">
                  <span>原始计划摘要</span>
                  <strong>{{ rawPlanSummary }}</strong>
                </div>
                <div v-if="stepEntries.length" class="debug-step-list">
                  <div v-for="step in stepEntries" :key="step.key" class="debug-step-item">
                    <div class="stack-header">
                      <strong>{{ step.label }}</strong>
                      <el-tag :type="statusTagType(step.status)" size="small" effect="plain">
                        {{ statusLabel(step.status) }}
                      </el-tag>
                    </div>
                    <div class="stack-body">{{ step.summary }}</div>
                    <div v-if="step.meta" class="stack-debug">{{ step.meta }}</div>
                  </div>
                </div>
              </div>
            </details>
          </template>

          <template v-else>
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
            <div v-else class="empty-state">发送问题后，这里会展示本轮 Agent 的执行过程。</div>
          </template>
        </div>
      </div>

      <div class="panel-section compact-section">
        <div class="section-title">运行状态</div>
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
      </div>

      <div class="panel-section compact-section">
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
const showTraceDetail = ref(false)

const STEP_LABELS = {
  message_understanding: '消息理解',
  message_understanding_agent: '消息理解智能体',
  execution_plan: '执行计划',
  message_orchestrator: '执行计划',
  dossier_memory_subject_reference: '主体指代解析',
  assistant_acknowledgement: '案情确认回复',
  legal_agent_answer: '法律回答生成',
  fact_confirmation: '上一条事实确认',
  fact_persistence: '案情结构化写入',
  graph_update: '图谱更新调度',
  dossier_intelligence: '案卷智能分析',
  participant_resolution: '主体关系确认',
  summary_update: '案卷摘要更新',
  legacy_fallback: '传统流程回退',
}

const TOOL_LABELS = {
  exact_law_lookup: '精确法条查询',
  legal_rag_search: '法律资料检索',
  case_search: '案例检索',
  case_fact_extract: '案情事实抽取',
  case_summary_update: '案情摘要写入',
  conversation_graph_extract: '图谱抽取',
  schedule_graph_update: '图谱更新调度',
  graph_update_schedule: '图谱更新调度',
  structured_case_persistence: '案情结构化写入',
  current_conversation_graph_query: '当前会话图谱查询',
  current_conversation_messages_query: '当前会话消息查询',
  dossier_graph_context_query: '案卷图谱查询',
  display_graph_build: '展示图谱构建',
  dossier_intelligence_build: '案卷智能分析',
  subject_reference_resolve: '主体指代解析',
  participant_resolution_apply: '主体确认写入',
  party_naming_confirm: '主体命名确认',
  legal_answer_compose: '法律回答生成',
  memory_answer_compose: '记忆回答生成',
  acknowledgement_compose: '案情确认回复',
}

const ROUTE_LABELS = {
  legal_qa: '法律问答',
  case_update: '案情更新',
  dossier_memory: '案卷记忆',
  general_consult: '一般咨询',
  document_assist: '文书辅助',
  greeting: '问候',
  out_of_scope: '超出范围',
  unknown: '待判断',
}

const RESPONSE_MODE_LABELS = {
  acknowledge_and_persist: '记录并确认',
  case_analysis_without_persistence: '仅分析不写入',
  legal_answer: '法律回答',
  dossier_memory_answer: '记忆回答',
  memory_answer: '记忆回答',
  clarification: '需要澄清',
  legacy_document_fallback: '文书辅助',
}

const STATUS_LABELS = {
  success: '已完成',
  skipped: '已跳过',
  failed: '失败',
  fallback: '降级处理',
  scheduled: '已调度',
  running: '执行中',
  pending: '等待中',
  idle: '空闲',
  planned: '等待中',
  blocked: '已阻断',
  applied: '已写入',
  error: '失败',
}

const CATEGORY_LABELS = {
  retrieval: '检索',
  composition: '生成',
  graph_read: '图谱读取',
  memory: '记忆读取',
  extraction: '信息抽取',
  graph_write: '写入',
  persistence_write: '写入',
  participant_write: '写入',
  intelligence: '案卷智能',
}

const DISPATCH_LABELS = {
  planner_direct: '执行计划',
  specialist_owned: '专员处理',
  planned_contract: '计划登记',
  legacy_single_call: '旧版单次调用',
}

const SPECIALIST_LABELS = {
  legal_qa_agent: '法律问答专员',
  case_update_agent: '案情更新专员',
  dossier_memory_agent: '案卷记忆专员',
  legacy_document_specialist: '文书辅助专员',
  legacy_fallback: '传统流程',
}

const GUARD_LABELS = {
  temporary_blocks_structured_persistence: '临时分析模式阻断案情写入',
  temporary_blocks_graph_update: '临时分析模式阻断图谱写入',
  temporary_blocks_persistence: '临时分析模式阻断案情和图谱写入',
  legal_qa_blocks_case_graph_pollution: '法律问答不写入案情或图谱',
  legal_qa_blocks_graph: '法律问答不写入案情或图谱',
  subject_reference_blocks_writes: '主体指代不写入案情或图谱',
  missing_previous_fact_blocks_confirm_writes: '未找到可确认的上一条事实，已阻断写入',
  missing_previous_fact: '未找到可确认的上一条事实，已阻断写入',
  unsupported_write_type_blocks_execution: '当前写入类型暂不支持执行',
  message_bound_graph_update_only: '图谱更新必须绑定明确的事实文本',
  dossier_followup_question_blocks_persistence: '案卷追问不写入案情或图谱',
  non_persistable_case_update: '当前消息不满足案情写入条件',
}

const FINAL_SOURCE_LABELS = {
  legal_agent: '法律问答专员',
  memory_resolver: '记忆解析器',
  case_update_acknowledgement: '案情确认回复',
  legacy_fallback: '传统流程回退',
  conversation_store: '会话存储',
}

const LEGACY_FALLBACK_REASON_LABELS = {
  message_understanding_fallback: '消息理解失败，已回退到传统流程',
  case_update_acknowledgement_failed: '案情确认回复失败，已回退到传统流程',
  legal_answer_compose_failed: '法律回答生成失败，已回退到传统流程',
  missing_previous_fact: '未找到可确认的上一条事实',
  no_actionable_execution_plan: '未形成可执行计划',
}

const executionTrace = computed(() =>
  props.analysis.executionTrace && typeof props.analysis.executionTrace === 'object'
    ? props.analysis.executionTrace
    : null,
)

const hasExecutionTrace = computed(() => Boolean(executionTrace.value))

const requestStateLabel = computed(() => statusLabel(props.analysis.requestState || 'idle'))
const routeIntentLabel = computed(() =>
  routeLabel(
    props.analysis.routeIntent ||
      executionTrace.value?.agent_plan?.route ||
      executionTrace.value?.execution_plan?.primary_route,
  ),
)

const traceSourceLabel = computed(() => (hasExecutionTrace.value ? '执行轨迹' : '旧版回退'))

const currentRouteKey = computed(() =>
  normalizeKey(
    agentPlan.value.route ||
      executionTrace.value?.execution_plan?.primary_route ||
      props.analysis.routeIntent,
  ),
)

const currentIntentKey = computed(() =>
  normalizeKey(agentPlan.value.intent || executionTrace.value?.message_understanding?.intent),
)

const compactSummaryLine = computed(() => {
  const route = currentRouteKey.value
  const intent = currentIntentKey.value

  if (route === 'legal_qa') {
    return compactMeta([
      '法律问答',
      hasSuccessfulTool(['exact_law_lookup']) ? '已查询法条' : '',
      hasSuccessfulTool(['legal_rag_search']) ? '已检索资料' : '',
      hasSuccessfulTool(['legal_answer_compose']) ? '已生成回答' : '',
      '未写入案情/图谱',
    ])
  }

  if (route === 'dossier_memory') {
    return compactMeta([
      '案卷记忆',
      hasSuccessfulTool(['current_conversation_graph_query']) ? '已查询当前会话图谱' : '',
      hasSuccessfulTool(['subject_reference_resolve']) ? '已解析主体指代' : '',
      '未写入案情/图谱',
    ])
  }

  if (route === 'case_update') {
    return compactMeta([
      '案情更新',
      intent === 'confirm_previous_fact' ? '已确认上一条事实' : '已记录事实',
      compactWriteStateLabel(),
    ])
  }

  if (route === 'document_assist') {
    return compactMeta(['文书辅助', hasExecutionTrace.value ? '已进入处理流程' : '', '按现有能力生成'])
  }

  if (fallbackActionEntries.value.length) {
    return compactMeta(['传统流程', `${fallbackActionEntries.value.length} 个步骤已完成`])
  }

  return '等待当前会话请求触发后端执行状态。'
})

const compactMetaLine = computed(() =>
  compactMeta([
    compactLatencyLabel.value ? `耗时 ${compactLatencyLabel.value}` : '',
    compactConfidenceLabel.value ? `置信度 ${compactConfidenceLabel.value}` : '',
  ]),
)

const compactLatencyLabel = computed(() => formatDuration(props.analysis.latencyMs))

const compactConfidenceLabel = computed(() => {
  const confidence = Number(props.analysis.confidenceScore)
  return Number.isFinite(confidence) && confidence > 0 ? confidence.toFixed(2) : ''
})

const compactSources = computed(() => {
  const sources = []

  for (const source of props.analysis.sources || []) {
    addSource(sources, normalizeSourceLabel(source))
  }

  for (const item of Array.isArray(executionTrace.value?.final_basis?.evidence)
    ? executionTrace.value.final_basis.evidence
    : []) {
    addSource(sources, sourceFromFinalBasisEvidence(item))
  }

  const memoryResolution = executionTrace.value?.memory_resolution
  if (memoryResolution && typeof memoryResolution === 'object') {
    for (const item of Array.isArray(memoryResolution.used_evidence) ? memoryResolution.used_evidence : []) {
      addSource(sources, sourceFromMemoryEvidence(item, memoryResolution.selected_scope))
    }
  }

  for (const tool of Array.isArray(executionTrace.value?.tool_calls) ? executionTrace.value.tool_calls : []) {
    addSource(sources, sourceFromToolCall(tool))
  }

  for (const write of Array.isArray(executionTrace.value?.writes?.results) ? executionTrace.value.writes.results : []) {
    addSource(sources, sourceFromWriteResult(write))
  }

  addSource(sources, fallbackCompactSource())

  return sources.slice(0, 3)
})

const latencyLabel = computed(() => {
  const latency = Number(props.analysis.latencyMs)
  return Number.isFinite(latency) && latency >= 0 ? `${Math.round(latency)} ms` : '暂无'
})

const confidenceLabel = computed(() => {
  const confidence = Number(props.analysis.confidenceScore)
  return Number.isFinite(confidence) && confidence > 0 ? confidence.toFixed(2) : '暂无'
})

const graphSyncSummary = computed(() => normalizeText(props.analysis.graphSyncSummary))

const agentPlan = computed(() =>
  executionTrace.value?.agent_plan && typeof executionTrace.value.agent_plan === 'object'
    ? executionTrace.value.agent_plan
    : {},
)

const rawPlanSummary = computed(() => normalizeText(agentPlan.value.plan_summary))

const agentPlanSummary = computed(() => formatPlanSummary(executionTrace.value))

const agentPlanRouteLabel = computed(() => {
  const route = routeLabel(
    agentPlan.value.route || executionTrace.value?.execution_plan?.primary_route || props.analysis.routeIntent,
  )
  const mode = responseModeLabel(agentPlan.value.response_style)
  return mode ? `${route} · ${mode}` : route
})

const agentPlanMeta = computed(() => {
  const parts = []
  const specialist = specialistLabel(agentPlan.value.specialist)
  const tools = Array.isArray(agentPlan.value.tool_names) ? agentPlan.value.tool_names.length : 0

  if (specialist) parts.push(`处理角色：${specialist}`)
  if (tools) parts.push(`调用 ${tools} 个工具`)

  return parts.join(' · ')
})

const toolEntries = computed(() =>
  (Array.isArray(executionTrace.value?.tool_calls) ? executionTrace.value.tool_calls : []).map((tool, index) => ({
    key: `${normalizeKey(tool.name)}-${index}`,
    label: toolLabel(tool.name),
    status: normalizeKey(tool.status) || 'planned',
    summary: summarizeToolCall(tool),
    meta: compactMeta([
      categoryLabel(tool.registry_category || tool.category),
      dispatchLabel(tool.dispatch_mode),
      formatLatency(tool.latency_ms),
    ]),
    debugMeta: compactMeta([
      normalizeText(tool.name),
      normalizeText(tool.current_implementation),
      normalizeText(tool.error),
    ]),
  })),
)

const guardEntries = computed(() => {
  const guards = Array.isArray(executionTrace.value?.guards_applied) ? executionTrace.value.guards_applied : []
  const deduped = new Map()

  guards
    .filter((guard) => guard && typeof guard === 'object' && guard.triggered)
    .forEach((guard) => {
      const label = guardLabel(guard.name)
      if (!deduped.has(label)) {
        deduped.set(label, {
          key: `${normalizeKey(guard.name)}-${deduped.size}`,
          label,
          meta: normalizeText(guard.name) && label !== normalizeText(guard.name) ? normalizeText(guard.name) : '',
        })
      }
    })

  return [...deduped.values()]
})

const writeEntries = computed(() => {
  const writeResults = Array.isArray(executionTrace.value?.writes?.results)
    ? executionTrace.value.writes.results
    : []

  if (writeResults.length) {
    return writeResults.map((write, index) => ({
      key: `${normalizeKey(write.write_type || write.name)}-${index}`,
      label: writeLabel(write.write_type || write.name),
      status: normalizeKey(write.status) || 'planned',
      summary: summarizeWriteResult(write),
      meta: compactMeta([formatLatency(write.latency_ms)]),
    }))
  }

  const requested = Array.isArray(executionTrace.value?.writes?.requested)
    ? executionTrace.value.writes.requested
    : []

  return requested.map((item, index) => ({
    key: `${normalizeKey(item.name)}-${index}`,
    label: writeLabel(item.name),
    status: item.allowed === false ? 'blocked' : 'planned',
    summary:
      item.allowed === false
        ? `已阻断：${formatBlockedBy(item.blocked_by)}`
        : normalizeText(item.reason) || '已登记写入请求。',
    meta: '',
  }))
})

const writeEmptyText = computed(() => '无写入。')

const guardEmptyText = computed(() => {
  if (currentRouteKey.value === 'legal_qa') {
    return '法律问答不写入案情或图谱。'
  }
  if (currentRouteKey.value === 'dossier_memory') {
    return '主体指代不写入案情或图谱。'
  }
  return '本轮未触发额外保护规则。'
})

const finalBasis = computed(() => {
  const payload = executionTrace.value?.final_basis
  if (!payload || typeof payload !== 'object') {
    return { summary: '', source: '', evidence: [] }
  }

  return {
    summary: normalizeText(payload.summary),
    source: finalSourceLabel(payload.source),
    evidence: Array.isArray(payload.evidence)
      ? payload.evidence.map((item) => formatEvidenceItem(item)).filter(Boolean)
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
    reason: legacyFallbackReasonLabel(payload.reason),
  }
})

const stepEntries = computed(() =>
  (Array.isArray(executionTrace.value?.steps) ? executionTrace.value.steps : []).map((step, index) => ({
    key: `${normalizeKey(step.name)}-${index}`,
    label: stepLabel(step.name),
    status: normalizeKey(step.status) || 'success',
    summary: buildStepSummary(step),
    meta: compactMeta([
      normalizeText(step.agent) ? `处理单元：${normalizeText(step.agent)}` : '',
      normalizeText(step.provider) || normalizeText(step.model)
        ? `模型：${[normalizeText(step.provider), normalizeText(step.model)].filter(Boolean).join(' / ')}`
        : '',
      formatLatency(step.latency_ms),
    ]),
  })),
)

const fallbackActionEntries = computed(() =>
  (props.analysis.actions || []).map((action, index) => ({
    key: `${normalizeKey(action?.tool)}-${index}`,
    label: toolLabel(action?.tool),
    status: normalizeKey(action?.status) || 'success',
    summary: normalizeText(action?.result_summary) || '本轮已完成处理。',
  })),
)

const requestIdLabel = computed(() => normalizeText(executionTrace.value?.request_id))

const showTechnicalDetails = computed(
  () => Boolean(requestIdLabel.value || rawPlanSummary.value || stepEntries.value.length),
)

function normalizeText(value) {
  return String(value || '').trim()
}

function normalizeKey(value) {
  return normalizeText(value).toLowerCase()
}

function compactMeta(parts) {
  return parts.filter(Boolean).join(' · ')
}

function addSource(sources, value) {
  const label = truncateText(normalizeText(value), 54)
  if (label && !sources.includes(label)) {
    sources.push(label)
  }
}

function normalizeSourceLabel(source) {
  if (typeof source === 'string') return source
  if (source && typeof source === 'object') {
    return (
      source.label ||
      source.title ||
      source.source ||
      source.name ||
      source.domain ||
      source.file ||
      ''
    )
  }
  return ''
}

function sourceFromFinalBasisEvidence(value) {
  const text = normalizeText(value)
  if (!text) return ''
  if (text.startsWith('confirmed_fact=')) {
    return `上一条用户事实：${text.slice('confirmed_fact='.length)}`
  }
  if (text.startsWith('fact_input=')) {
    return `用户本轮补充事实：${text.slice('fact_input='.length)}`
  }
  if (text.startsWith('resolved_entity=')) {
    const scope = scopeLabel(executionTrace.value?.memory_resolution?.selected_scope)
    return `${scope || '当前会话图谱'}：${text.slice('resolved_entity='.length)}`
  }
  if (/documents=(\d+)/i.test(text)) {
    return `精确法条查询：命中 ${text.match(/documents=(\d+)/i)?.[1]} 条相关法条`
  }
  if (text.startsWith('guard=') || text.startsWith('specialist=') || text.startsWith('selected_scope=')) {
    return ''
  }
  return text
}

function sourceFromMemoryEvidence(item, selectedScope) {
  if (!item || typeof item !== 'object') return ''
  const label = normalizeText(
    item.resolved_label ||
      item.graph_node_label ||
      item.participant_label ||
      item.raw_label ||
      item.message,
  )
  if (!label) return ''
  return `${scopeLabel(selectedScope) || '记忆来源'}：${label}`
}

function sourceFromToolCall(tool) {
  const toolName = normalizeKey(tool?.name)
  const status = normalizeKey(tool?.status)
  const outputSummary = normalizeText(tool?.output_summary || tool?.result_summary)
  if (!['success', 'scheduled', 'applied'].includes(status)) return ''

  const documentMatch = outputSummary.match(/documents=(\d+)/i)
  if (toolName === 'exact_law_lookup' && documentMatch) {
    return `精确法条查询：命中 ${documentMatch[1]} 条相关法条`
  }
  if (toolName === 'legal_rag_search' && documentMatch) {
    return `法律资料检索：命中 ${documentMatch[1]} 条相关资料`
  }
  if (toolName === 'current_conversation_graph_query') {
    return '当前会话图谱：已查询'
  }
  return ''
}

function sourceFromWriteResult(write) {
  const writeType = normalizeKey(write?.write_type || write?.name)
  const status = normalizeKey(write?.status)
  const inputSummary = normalizeText(write?.input_summary)
  if (!['success', 'scheduled', 'applied'].includes(status) || !inputSummary) return ''

  if (currentIntentKey.value === 'confirm_previous_fact') {
    return `上一条用户事实：${inputSummary}`
  }
  if (writeType === 'structured_case_persistence' || writeType === 'graph_update_schedule') {
    return `用户本轮补充事实：${inputSummary}`
  }
  return ''
}

function fallbackCompactSource() {
  if (currentRouteKey.value === 'legal_qa' && hasSuccessfulTool(['exact_law_lookup', 'legal_rag_search'])) {
    return '法律检索：已完成'
  }
  if (currentRouteKey.value === 'dossier_memory' && hasSuccessfulTool(['current_conversation_graph_query'])) {
    return '当前会话图谱：已查询'
  }
  if (currentRouteKey.value === 'case_update') {
    const write = firstWriteWithInput()
    if (write && currentIntentKey.value === 'confirm_previous_fact') {
      return `上一条用户事实：${write.input_summary}`
    }
    if (write) {
      return `用户本轮补充事实：${write.input_summary}`
    }
  }
  return ''
}

function firstWriteWithInput() {
  return (Array.isArray(executionTrace.value?.writes?.results) ? executionTrace.value.writes.results : [])
    .find((write) => normalizeText(write?.input_summary))
}

function hasSuccessfulTool(names) {
  const allowed = new Set(names.map((item) => normalizeKey(item)))
  return (Array.isArray(executionTrace.value?.tool_calls) ? executionTrace.value.tool_calls : []).some((tool) => {
    const status = normalizeKey(tool?.status)
    return allowed.has(normalizeKey(tool?.name)) && !['failed', 'blocked', 'skipped'].includes(status)
  })
}

function compactWriteStateLabel() {
  if (hasSuccessfulWrite('graph_update_schedule')) return '已调度图谱更新'
  if (hasSuccessfulWrite('structured_case_persistence')) return '已写入案情'
  if (hasBlockedWrites()) return '写入已阻断'
  return '未写入案情/图谱'
}

function hasSuccessfulWrite(name) {
  return (Array.isArray(executionTrace.value?.writes?.results) ? executionTrace.value.writes.results : []).some(
    (write) =>
      normalizeKey(write?.write_type || write?.name) === normalizeKey(name) &&
      ['success', 'scheduled', 'applied'].includes(normalizeKey(write?.status)),
  )
}

function hasBlockedWrites() {
  const blocked = Array.isArray(executionTrace.value?.writes?.blocked)
    ? executionTrace.value.writes.blocked
    : []
  const results = Array.isArray(executionTrace.value?.writes?.results)
    ? executionTrace.value.writes.results
    : []
  return blocked.length > 0 || results.some((write) => normalizeKey(write?.status) === 'blocked')
}

function formatDuration(value) {
  const latency = Number(value)
  if (!Number.isFinite(latency) || latency < 0) return ''
  if (latency >= 1000) return `${(latency / 1000).toFixed(1)}s`
  return `${Math.round(latency)}ms`
}

function truncateText(value, maxLength) {
  const text = normalizeText(value)
  if (!text || text.length <= maxLength) return text
  return `${text.slice(0, maxLength)}...`
}

function formatLatency(value) {
  const latency = Number(value)
  return Number.isFinite(latency) && latency >= 0 ? `${Math.round(latency)} ms` : ''
}

function unknownLabel(prefix, value, emptyFallback) {
  const raw = normalizeText(value)
  if (!raw) return emptyFallback
  return `${prefix}：${raw}`
}

function stepLabel(name) {
  return STEP_LABELS[normalizeKey(name)] || unknownLabel('步骤', name, '未知步骤')
}

function toolLabel(name) {
  return TOOL_LABELS[normalizeKey(name)] || unknownLabel('工具', name, '未知工具')
}

function routeLabel(value) {
  return ROUTE_LABELS[normalizeKey(value)] || unknownLabel('流程', value, '待判断')
}

function responseModeLabel(value) {
  if (!normalizeText(value)) return ''
  return RESPONSE_MODE_LABELS[normalizeKey(value)] || unknownLabel('模式', value, '未知模式')
}

function statusLabel(value) {
  return STATUS_LABELS[normalizeKey(value)] || unknownLabel('状态', value, '未知状态')
}

function categoryLabel(value) {
  return CATEGORY_LABELS[normalizeKey(value)] || ''
}

function dispatchLabel(value) {
  return DISPATCH_LABELS[normalizeKey(value)] || ''
}

function specialistLabel(value) {
  return SPECIALIST_LABELS[normalizeKey(value)] || ''
}

function guardLabel(value) {
  return GUARD_LABELS[normalizeKey(value)] || unknownLabel('保护规则', value, '未知保护规则')
}

function writeLabel(value) {
  return TOOL_LABELS[normalizeKey(value)] || unknownLabel('写入动作', value, '未知写入动作')
}

function finalSourceLabel(value) {
  return FINAL_SOURCE_LABELS[normalizeKey(value)] || normalizeText(value)
}

function legacyFallbackReasonLabel(value) {
  return LEGACY_FALLBACK_REASON_LABELS[normalizeKey(value)] || normalizeText(value)
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

function formatPlanSummary(trace) {
  const route = normalizeKey(trace?.agent_plan?.route || trace?.execution_plan?.primary_route)

  if (route === 'legal_qa') {
    return '法律问答：查询相关法条或资料并生成回答，不写入案情或图谱。'
  }
  if (route === 'case_update') {
    return '案情更新：记录用户补充的事实，并按保护规则决定是否写入案情和图谱。'
  }
  if (route === 'dossier_memory') {
    return '案卷记忆：读取当前会话或案卷记忆，回答主体指代或案卷上下文问题。'
  }
  if (route === 'document_assist') {
    return '文书辅助：根据用户请求进入文书处理流程，并按现有能力生成结果。'
  }
  return '系统已根据消息内容选择合适的处理流程。'
}

function summarizeToolCall(tool) {
  const toolName = normalizeKey(tool?.name)
  const status = normalizeKey(tool?.status)
  const outputSummary = normalizeText(tool?.output_summary || tool?.result_summary)
  const inputSummary = normalizeText(tool?.input_summary || tool?.arguments_summary)

  if (status === 'blocked') {
    return `已阻断：${blockedReasonLabel(tool?.blocked_reason)}`
  }
  if (status === 'skipped') {
    return blockedReasonLabel(tool?.blocked_reason || outputSummary) || '本轮未执行该工具。'
  }
  if (status === 'failed') {
    return normalizeText(tool?.error) ? `执行失败：${normalizeText(tool.error)}` : '执行失败。'
  }

  const documentMatch = outputSummary.match(/documents=(\d+)/i)
  const messageMatch = outputSummary.match(/recent_messages=(\d+)/i)
  const graphMatch = outputSummary.match(/nodes=(\d+)\s+edges=(\d+)/i)

  if (toolName === 'exact_law_lookup' && documentMatch) {
    return `查询到 ${documentMatch[1]} 条相关法条。`
  }
  if (toolName === 'legal_rag_search' && documentMatch) {
    return `检索到 ${documentMatch[1]} 条相关资料。`
  }
  if (toolName === 'case_search' && documentMatch) {
    return `检索到 ${documentMatch[1]} 条相关案例。`
  }
  if (toolName === 'current_conversation_graph_query' && graphMatch) {
    return `读取到 ${graphMatch[1]} 个节点、${graphMatch[2]} 条关系。`
  }
  if (toolName === 'current_conversation_messages_query' && messageMatch) {
    return `读取到 ${messageMatch[1]} 条近期消息。`
  }
  if (toolName === 'subject_reference_resolve') {
    return outputSummary || '已完成主体指代解析。'
  }
  if (toolName === 'memory_answer_compose') {
    return '已生成记忆回答。'
  }
  if (toolName === 'legal_answer_compose') {
    return '已生成法律回答。'
  }
  if (toolName === 'acknowledgement_compose') {
    return '已生成案情确认回复。'
  }

  return outputSummary || inputSummary || '已完成工具处理。'
}

function blockedReasonLabel(value) {
  const normalized = normalizeKey(value)
  if (!normalized) return ''
  return GUARD_LABELS[normalized] || normalizeText(value)
}

function formatBlockedBy(blockedBy) {
  return Array.isArray(blockedBy) && blockedBy.length
    ? blockedBy.map((item) => guardLabel(item)).join('、')
    : '当前写入已被阻断'
}

function summarizeWriteResult(write) {
  const writeType = normalizeKey(write?.write_type || write?.name)
  const status = normalizeKey(write?.status)
  const outputSummary = normalizeText(write?.output_summary)
  const error = normalizeText(write?.error)

  if (status === 'success') {
    if (writeType === 'structured_case_persistence') {
      return '已写入案情摘要和结构化状态。'
    }
    if (writeType === 'graph_update_schedule') {
      return '已调度图谱更新。'
    }
    return outputSummary || '写入已完成。'
  }

  if (status === 'blocked') {
    return `已阻断：${formatBlockedBy(write?.blocked_by)}`
  }

  if (status === 'skipped') {
    return blockedReasonLabel(write?.skip_reason || outputSummary) || '本轮未执行写入。'
  }

  if (status === 'failed') {
    return error ? `执行失败：${error}` : '写入执行失败。'
  }

  return outputSummary || '写入已登记。'
}

function formatEvidenceItem(value) {
  const text = normalizeText(value)
  if (!text) return ''

  if (text.startsWith('selected_scope=')) {
    return `使用范围：${scopeLabel(text.slice('selected_scope='.length))}`
  }
  if (text.startsWith('resolved_entity=')) {
    return `识别主体：${text.slice('resolved_entity='.length)}`
  }
  if (text.startsWith('confirmed_fact=')) {
    return `已绑定事实：${text.slice('confirmed_fact='.length)}`
  }
  if (text.startsWith('fact_input=')) {
    return `事实输入：${text.slice('fact_input='.length)}`
  }
  if (text.startsWith('guard=')) {
    return `保护规则：${guardLabel(text.slice('guard='.length))}`
  }
  if (text.startsWith('specialist=')) {
    const raw = text.slice('specialist='.length)
    return `处理方式：${specialistLabel(raw) || toolLabel(raw) || raw}`
  }
  if (text.startsWith('fallback_reason=')) {
    return `降级原因：${legacyFallbackReasonLabel(text.slice('fallback_reason='.length))}`
  }

  return text
}

function scopeLabel(value) {
  const mapping = {
    current_conversation_graph: '当前会话图谱',
    current_conversation_messages: '当前会话消息',
    dossier_confirmed_entities: '已确认案卷主体',
    dossier_cross_conversation_graph: '跨会话案卷图谱',
  }
  return mapping[normalizeKey(value)] || normalizeText(value)
}

function buildStepSummary(step) {
  const details = step?.details && typeof step.details === 'object' ? step.details : {}
  const status = normalizeKey(step?.status)

  if (status === 'skipped') {
    return blockedReasonLabel(step?.skip_reason) || unknownLabel('跳过原因', step?.skip_reason, '该步骤已跳过。')
  }
  if (status === 'fallback') {
    return legacyFallbackReasonLabel(step?.fallback_reason) || '该步骤已回退到传统流程。'
  }
  if (status === 'failed') {
    return normalizeText(details.error) || '该步骤执行失败。'
  }
  if (normalizeText(details.selected_scope)) {
    return `已选择作用范围：${scopeLabel(details.selected_scope)}`
  }
  if (normalizeText(details.task_id)) {
    return `已调度任务：${normalizeText(details.task_id)}`
  }
  if (normalizeText(details.input_summary)) {
    return normalizeText(details.input_summary)
  }
  if (normalizeText(details.output_summary)) {
    return normalizeText(details.output_summary)
  }
  if (normalizeText(details.graph_update_source)) {
    return `图谱更新来源：${normalizeText(details.graph_update_source)}`
  }
  if (normalizeText(details.resolved_previous_fact_text)) {
    return `已绑定上一条事实：${normalizeText(details.resolved_previous_fact_text)}`
  }

  return '该步骤已完成。'
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

.primary-section {
  background:
    radial-gradient(circle at top right, rgba(37, 99, 235, 0.12), transparent 32%),
    linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
}

.graph-first {
  order: 0;
}

.graph-section {
  background:
    radial-gradient(circle at top right, rgba(59, 130, 246, 0.1), transparent 30%),
    linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
}

.agent-compact-section {
  order: 1;
  padding: 12px 14px;
}

.compact-section {
  padding: 12px 14px;
}

.agent-compact-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.agent-summary-line {
  margin-top: 3px;
  font-size: 12px;
  line-height: 1.5;
  color: #334155;
}

.agent-meta-line {
  margin-top: 7px;
  font-size: 11px;
  color: #64748b;
}

.compact-warning {
  margin-top: 8px;
}

.compact-sources {
  margin-top: 10px;
}

.compact-subsection-title {
  margin-bottom: 6px;
}

.compact-source-list {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.compact-source-item {
  padding: 6px 8px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.78);
  border: 1px solid rgba(148, 163, 184, 0.16);
  font-size: 11px;
  line-height: 1.45;
  color: #475569;
}

.compact-empty {
  padding: 8px 10px;
}

.trace-toggle {
  margin-top: 6px;
  padding-left: 0;
}

.agent-trace-detail {
  max-height: 420px;
  overflow-y: auto;
  margin-top: 8px;
  padding-top: 10px;
  border-top: 1px solid rgba(148, 163, 184, 0.16);
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
  line-height: 1.6;
  color: #64748b;
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

.stack-list,
.debug-step-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.stack-item-warning {
  background: rgba(255, 251, 235, 0.92);
  border-color: rgba(245, 158, 11, 0.24);
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
  color: #64748b;
}

.stack-debug {
  margin-top: 6px;
  font-size: 10px;
  color: #94a3b8;
  word-break: break-all;
}

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
  margin-top: 12px;
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(255, 247, 237, 0.92);
  border: 1px solid rgba(251, 146, 60, 0.34);
  color: #9a3412;
  font-size: 12px;
  line-height: 1.6;
}

.graph-sync-tip {
  margin-bottom: 10px;
  padding: 8px 10px;
  border-radius: 10px;
  background: rgba(239, 246, 255, 0.88);
  border: 1px solid rgba(96, 165, 250, 0.28);
  color: #1d4ed8;
  font-size: 12px;
  line-height: 1.5;
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

.empty-state {
  font-size: 12px;
  color: #64748b;
  line-height: 1.6;
}

.debug-details {
  margin-top: 14px;
  border-radius: 12px;
  border: 1px dashed rgba(148, 163, 184, 0.28);
  background: rgba(248, 250, 252, 0.58);
}

.debug-details summary {
  cursor: pointer;
  list-style: none;
  padding: 10px 12px;
  font-size: 12px;
  font-weight: 600;
  color: #64748b;
}

.debug-details summary::-webkit-details-marker {
  display: none;
}

.debug-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 0 12px 12px;
}

.debug-line {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 11px;
  color: #64748b;
}

.debug-line strong {
  font-size: 11px;
  color: #475569;
  word-break: break-all;
}

.debug-step-item {
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.82);
  border: 1px solid rgba(148, 163, 184, 0.14);
}

.rating-row {
  margin-bottom: 8px;
}

.submit-btn {
  margin-top: 8px;
  width: 100%;
}

@media (max-width: 768px) {
  .meta-grid {
    grid-template-columns: 1fr;
  }
}
</style>

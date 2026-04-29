<template>
  <div class="graph-panel">
    <div class="graph-meta">
      <div class="meta-block">
        <span class="meta-label">{{ graph.metaLabel || '案情图谱' }}</span>
        <span class="meta-value">{{ graph.query || '等待会话触发图谱生成' }}</span>
      </div>
      <div class="meta-block compact">
        <span class="meta-pill">{{ graphNodes.length }} 节点</span>
        <span class="meta-pill">{{ graphEdges.length }} 关系</span>
      </div>
    </div>

    <div v-if="resolvedLegendItems.length" class="legend">
      <span v-for="item in resolvedLegendItems" :key="item.category" class="legend-item">
        <span class="legend-dot" :style="{ background: colorForCategory(item.category) }"></span>
        {{ item.label }}
      </span>
    </div>

    <div class="graph-canvas">
      <div v-if="graph.loading" class="graph-overlay">
        <el-icon class="rotating"><Loading /></el-icon>
        <span>{{ graph.loadingText || '正在加载当前会话的案情图谱...' }}</span>
      </div>
      <div v-else-if="graph.error" class="graph-overlay error">
        <el-icon><Warning /></el-icon>
        <span>{{ graph.error }}</span>
      </div>
      <div v-else-if="!layout.nodes.length" class="graph-overlay empty">
        <el-icon><Share /></el-icon>
        <span>{{ graph.emptyText || '当前会话尚未形成可视化案情图谱' }}</span>
      </div>

      <svg
        v-else
        class="graph-svg"
        :viewBox="`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`"
        role="img"
        :aria-label="graph.ariaLabel || '动态案情图谱'"
      >
        <g v-for="edge in layout.edges" :key="edge.id">
          <line
            :x1="edge.x1"
            :y1="edge.y1"
            :x2="edge.x2"
            :y2="edge.y2"
            class="edge-line"
          />
          <text :x="edge.labelX" :y="edge.labelY" class="edge-label">
            {{ edge.label }}
          </text>
        </g>

        <g
          v-for="node in layout.nodes"
          :key="node.id"
          class="node-group"
          @click="selectedNodeId = node.id"
        >
          <circle
            :cx="node.x"
            :cy="node.y"
            :r="selectedNodeId === node.id ? 28 : 24"
            :fill="colorForCategory(node.category)"
            :class="['node-circle', { active: selectedNodeId === node.id }]"
          />
          <text :x="node.x" :y="node.y + 5" class="node-label">
            {{ shortLabel(node.label) }}
          </text>
        </g>
      </svg>
    </div>

    <div v-if="selectedNode" class="node-detail">
      <div class="detail-head">
        <span class="detail-title">{{ selectedNode.label }}</span>
        <el-tag size="small" effect="plain">
          {{ selectedNode.categoryLabel || categoryLabelFor(selectedNode.category) }}
        </el-tag>
      </div>
      <div v-if="selectedNodeLabels.length" class="detail-row">
        <span class="detail-key">标签</span>
        <span class="detail-value">{{ selectedNodeLabels.join(' / ') }}</span>
      </div>
      <div
        v-for="entry in selectedDetailRows"
        :key="entry.key"
        class="detail-row"
      >
        <span class="detail-key">{{ entry.key }}</span>
        <span class="detail-value">{{ entry.value }}</span>
      </div>
      <div v-if="selectedSourceConversations.length" class="source-conversation-list">
        <div class="source-conversation-title">来源会话</div>
        <div
          v-for="conversation in selectedSourceConversations"
          :key="conversation.conversation_id"
          class="source-conversation-item"
        >
          <div class="source-conversation-main">
            <span class="source-conversation-name">
              {{ conversation.title || `会话 ${conversation.conversation_id}` }}
            </span>
            <el-tag v-if="conversation.is_best" size="small" type="success" effect="plain">
              最佳会话
            </el-tag>
          </div>
          <div class="source-conversation-meta">
            <span>{{ `原始标签：${conversation.source_labels.length ? conversation.source_labels.join(' / ') : '未标注'}` }}</span>
            <span>{{ `相关关系：${conversation.relation_count}` }}</span>
            <span>{{ `相关事实：${conversation.fact_count}` }}</span>
          </div>
          <el-button
            size="small"
            type="primary"
            text
            @click="$emit('node-action', { ...selectedNode, actionConversationId: conversation.conversation_id })"
          >
            查看该会话
          </el-button>
        </div>
      </div>
      <div v-if="selectedNode.detailNote" class="detail-note">
        {{ selectedNode.detailNote }}
      </div>
      <details v-if="selectedDebugRows.length" class="node-debug-detail">
        <summary>高级详情</summary>
        <div
          v-for="entry in selectedDebugRows"
          :key="entry.key"
          class="detail-row"
        >
          <span class="detail-key">{{ entry.key }}</span>
          <span class="detail-value">{{ entry.value }}</span>
        </div>
      </details>
      <div v-if="selectedNode.actionLabel" class="node-detail-actions">
        <el-button
          size="small"
          type="primary"
          plain
          :disabled="Boolean(selectedNode.actionDisabled)"
          @click="$emit('node-action', selectedNode)"
        >
          {{ selectedNode.actionLabel }}
        </el-button>
        <span v-if="selectedNode.actionHint" class="node-detail-action-hint">
          {{ selectedNode.actionHint }}
        </span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

const VIEW_WIDTH = 520
const VIEW_HEIGHT = 440
const DEFAULT_CATEGORY = '未分类'
const DEFAULT_LEGEND_ITEMS = [
  { category: '主体', label: '主体', color: '#2563eb' },
  { category: '客体', label: '客体', color: '#f59e0b' },
  { category: '事件', label: '事件', color: '#dc2626' },
  { category: '后果', label: '后果', color: '#16a34a' },
]
const FALLBACK_COLORS = ['#2563eb', '#f59e0b', '#7c3aed', '#64748b', '#16a34a', '#dc2626', '#0891b2']

const props = defineProps({
  graph: {
    type: Object,
    default: () => ({
      query: '',
      nodes: [],
      edges: [],
      loading: false,
      error: '',
    }),
  },
})

defineEmits(['node-action'])

const selectedNodeId = ref('')

const graphNodes = computed(() => (Array.isArray(props.graph.nodes) ? props.graph.nodes : []))
const graphEdges = computed(() => (Array.isArray(props.graph.edges) ? props.graph.edges : []))
const resolvedLegendItems = computed(() => {
  const items = Array.isArray(props.graph.legendItems) ? props.graph.legendItems : []
  return items.length ? items : DEFAULT_LEGEND_ITEMS
})
const legendLookup = computed(() => {
  const lookup = {}
  for (const item of resolvedLegendItems.value) {
    const category = normalizeCategory(item?.category)
    lookup[category] = {
      label: String(item?.label || category).trim() || category,
      color: String(item?.color || '').trim(),
    }
  }
  return lookup
})
const layoutCategories = computed(() => {
  const categories = [
    ...resolvedLegendItems.value.map((item) => normalizeCategory(item?.category)),
    ...graphNodes.value.map((node) => normalizeCategory(node?.category || node?.type)),
  ].filter(Boolean)
  return categories.filter((category, index) => categories.indexOf(category) === index)
})
const layout = computed(() =>
  buildForceLayout(
    graphNodes.value,
    graphEdges.value,
    buildCategoryAnchors(layoutCategories.value)
  )
)

const selectedNode = computed(() =>
  layout.value.nodes.find((node) => node.id === selectedNodeId.value) || layout.value.nodes[0] || null
)

const selectedNodeLabels = computed(() => {
  const labels = Array.isArray(selectedNode.value?.labels)
    ? selectedNode.value.labels.map((item) => String(item || '').trim()).filter(Boolean)
    : []
  return labels.filter((label, index) => labels.indexOf(label) === index)
})

const selectedDetailRows = computed(() => {
  const detailRows = Array.isArray(selectedNode.value?.detailRows)
    ? selectedNode.value.detailRows
        .map((entry) => ({
          key: String(entry?.key || '').trim(),
          value: String(entry?.value || '').trim(),
        }))
        .filter((entry) => entry.key && entry.value)
    : []
  if (detailRows.length) {
    return detailRows.slice(0, 10)
  }
  if (!selectedNode.value?.properties) return []
  return Object.entries(selectedNode.value.properties)
    .filter(([, value]) => value !== null && value !== undefined && `${value}`.trim() !== '')
    .slice(0, 8)
    .map(([key, value]) => ({
      key,
      value: `${value}`.slice(0, 160),
    }))
})

const selectedDebugRows = computed(() => {
  const debugRows = Array.isArray(selectedNode.value?.debugRows) ? selectedNode.value.debugRows : []
  return debugRows
    .map((entry) => ({
      key: String(entry?.key || '').trim(),
      value: String(entry?.value || '').trim(),
    }))
    .filter((entry) => entry.key && entry.value)
})

const selectedSourceConversations = computed(() => {
  const items = Array.isArray(selectedNode.value?.sourceConversations)
    ? selectedNode.value.sourceConversations
    : Array.isArray(selectedNode.value?.source_conversations)
      ? selectedNode.value.source_conversations
      : []
  return items
    .map((item) => {
      const conversationId = Number(item?.conversation_id || 0)
      return {
        conversation_id: conversationId,
        title: String(item?.title || '').trim(),
        source_labels: Array.isArray(item?.source_labels)
          ? item.source_labels.map((label) => String(label || '').trim()).filter(Boolean)
          : [],
        fact_count: Number(item?.fact_count || 0),
        relation_count: Number(item?.relation_count || 0),
        is_best: Boolean(item?.is_best),
      }
    })
    .filter((item) => item.conversation_id > 0)
})

watch(
  () => graphNodes.value,
  (nodes) => {
    const currentNodeId = String(selectedNodeId.value || '').trim()
    const hasCurrent = nodes.some((node) => String(node?.id || '').trim() === currentNodeId)
    selectedNodeId.value = hasCurrent ? currentNodeId : nodes?.[0]?.id || ''
  },
  { immediate: true }
)

function shortLabel(value) {
  if (!value) return ''
  return value.length > 8 ? `${value.slice(0, 8)}...` : value
}

function normalizeCategory(value) {
  const normalized = String(value || '').trim()
  return normalized || DEFAULT_CATEGORY
}

function buildCategoryAnchors(categories) {
  const normalizedCategories = categories.length
    ? categories.map((item) => normalizeCategory(item))
    : [DEFAULT_CATEGORY]
  const columnCount = Math.min(Math.max(2, Math.ceil(Math.sqrt(normalizedCategories.length))), 3)
  const rowCount = Math.max(1, Math.ceil(normalizedCategories.length / columnCount))
  const anchors = {}

  normalizedCategories.forEach((category, index) => {
    const columnIndex = index % columnCount
    const rowIndex = Math.floor(index / columnCount)
    anchors[category] = {
      x: ((columnIndex + 1) / (columnCount + 1)) * VIEW_WIDTH,
      y: ((rowIndex + 1) / (rowCount + 1)) * VIEW_HEIGHT,
    }
  })

  anchors[DEFAULT_CATEGORY] ||= {
    x: VIEW_WIDTH / 2,
    y: VIEW_HEIGHT / 2,
  }

  return anchors
}

function categoryLabelFor(category) {
  const normalized = normalizeCategory(category)
  return legendLookup.value[normalized]?.label || normalized || DEFAULT_CATEGORY
}

function autoColorForCategory(category) {
  const normalized = normalizeCategory(category)
  let hash = 0
  for (let index = 0; index < normalized.length; index += 1) {
    hash = (hash * 31 + normalized.charCodeAt(index)) >>> 0
  }
  return FALLBACK_COLORS[hash % FALLBACK_COLORS.length]
}

function colorForCategory(category) {
  const normalized = normalizeCategory(category)
  return legendLookup.value[normalized]?.color || autoColorForCategory(normalized)
}

function buildForceLayout(nodes, edges, categoryAnchors) {
  if (!nodes.length) {
    return { nodes: [], edges: [] }
  }

  const state = nodes.map((node, index) => {
    const category = normalizeCategory(node.category || node.type)
    const anchor = categoryAnchors[category] || categoryAnchors[DEFAULT_CATEGORY]
    const angle = (Math.PI * 2 * index) / Math.max(nodes.length, 1)
    const radius = 40 + (index % 3) * 18

    return {
      ...node,
      category,
      x: anchor.x + Math.cos(angle) * radius,
      y: anchor.y + Math.sin(angle) * radius,
      vx: 0,
      vy: 0,
      anchorX: anchor.x,
      anchorY: anchor.y,
    }
  })

  const nodeMap = new Map(state.map((node) => [node.id, node]))
  const validEdges = edges.filter((edge) => nodeMap.has(edge.source) && nodeMap.has(edge.target))
  const REPULSION = 2600
  const TARGET_DISTANCE = 170
  const SPRING = 0.02
  const ANCHOR_FORCE = 0.01
  const DAMPING = 0.88

  for (let step = 0; step < 220; step += 1) {
    for (let i = 0; i < state.length; i += 1) {
      for (let j = i + 1; j < state.length; j += 1) {
        const a = state[i]
        const b = state[j]
        const dx = a.x - b.x
        const dy = a.y - b.y
        const distance = Math.sqrt(dx * dx + dy * dy) || 1
        const force = REPULSION / (distance * distance)
        const fx = (dx / distance) * force
        const fy = (dy / distance) * force
        a.vx += fx
        a.vy += fy
        b.vx -= fx
        b.vy -= fy
      }
    }

    for (const edge of validEdges) {
      const source = nodeMap.get(edge.source)
      const target = nodeMap.get(edge.target)
      const dx = target.x - source.x
      const dy = target.y - source.y
      const distance = Math.sqrt(dx * dx + dy * dy) || 1
      const force = (distance - TARGET_DISTANCE) * SPRING
      const fx = (dx / distance) * force
      const fy = (dy / distance) * force
      source.vx += fx
      source.vy += fy
      target.vx -= fx
      target.vy -= fy
    }

    for (const node of state) {
      node.vx += (node.anchorX - node.x) * ANCHOR_FORCE
      node.vy += (node.anchorY - node.y) * ANCHOR_FORCE
      node.vx *= DAMPING
      node.vy *= DAMPING
      node.x = Math.min(VIEW_WIDTH - 40, Math.max(40, node.x + node.vx))
      node.y = Math.min(VIEW_HEIGHT - 40, Math.max(40, node.y + node.vy))
    }
  }

  return {
    nodes: state,
    edges: validEdges.map((edge) => {
      const source = nodeMap.get(edge.source)
      const target = nodeMap.get(edge.target)
      return {
        ...edge,
        x1: source.x,
        y1: source.y,
        x2: target.x,
        y2: target.y,
        labelX: (source.x + target.x) / 2,
        labelY: (source.y + target.y) / 2 - 10,
      }
    }),
  }
}
</script>

<style scoped>
.graph-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.graph-meta {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: flex-start;
}

.meta-block {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.meta-block.compact {
  flex-direction: row;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.meta-label {
  font-size: 11px;
  color: #6b7280;
}

.meta-value {
  font-size: 12px;
  color: #111827;
  line-height: 1.5;
  word-break: break-word;
}

.meta-pill {
  font-size: 11px;
  line-height: 1;
  padding: 6px 8px;
  border-radius: 999px;
  background: #eef2ff;
  color: #4338ca;
}

.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: #4b5563;
}

.legend-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.graph-canvas {
  position: relative;
  min-height: 440px;
  border-radius: 16px;
  overflow: hidden;
  background:
    radial-gradient(circle at top, rgba(59, 130, 246, 0.12), transparent 34%),
    linear-gradient(180deg, #f8fafc 0%, #eef4ff 100%);
  border: 1px solid #dbeafe;
}

.graph-svg {
  width: 100%;
  height: 440px;
  display: block;
}

.graph-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  font-size: 13px;
  color: #475569;
  background: rgba(248, 250, 252, 0.92);
  z-index: 2;
  text-align: center;
  padding: 0 20px;
}

.graph-overlay.error {
  color: #b42318;
}

.graph-overlay.empty {
  color: #64748b;
}

.edge-line {
  stroke: rgba(71, 85, 105, 0.34);
  stroke-width: 1.4;
}

.edge-label {
  text-anchor: middle;
  font-size: 10px;
  fill: #475569;
}

.node-group {
  cursor: pointer;
}

.node-circle {
  stroke: rgba(255, 255, 255, 0.95);
  stroke-width: 2.5;
  transition: transform 0.2s ease, stroke 0.2s ease;
}

.node-circle.active {
  stroke: #0f172a;
  stroke-width: 3;
}

.node-label {
  font-size: 10px;
  font-weight: 600;
  fill: #ffffff;
  text-anchor: middle;
  pointer-events: none;
}

.node-detail {
  border-radius: 14px;
  border: 1px solid #e5e7eb;
  background: #ffffff;
  padding: 12px;
}

.detail-head {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: center;
  margin-bottom: 8px;
}

.detail-title {
  font-size: 13px;
  font-weight: 600;
  color: #111827;
}

.detail-row {
  display: grid;
  grid-template-columns: 68px 1fr;
  gap: 8px;
  padding: 4px 0;
}

.detail-key {
  font-size: 11px;
  color: #6b7280;
}

.detail-value {
  font-size: 12px;
  color: #374151;
  word-break: break-word;
  line-height: 1.6;
}

.detail-note {
  margin-top: 8px;
  font-size: 12px;
  color: #6b7280;
  line-height: 1.6;
}

.source-conversation-list {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.source-conversation-title {
  font-size: 12px;
  font-weight: 600;
  color: #111827;
}

.source-conversation-item {
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  padding: 8px;
  background: #f8fafc;
}

.source-conversation-main {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  align-items: center;
}

.source-conversation-name {
  font-size: 12px;
  font-weight: 600;
  color: #111827;
}

.source-conversation-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: 5px;
  font-size: 11px;
  color: #64748b;
  line-height: 1.5;
}

.node-debug-detail {
  margin-top: 8px;
  font-size: 11px;
  color: #64748b;
}

.node-debug-detail summary {
  cursor: pointer;
  color: #6b7280;
}

.node-detail-actions {
  margin-top: 10px;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.node-detail-action-hint {
  font-size: 11px;
  color: #6b7280;
}

.rotating {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }

  to {
    transform: rotate(360deg);
  }
}
</style>

<template>
  <div class="graph-panel">
    <div class="graph-meta">
      <div class="meta-block">
        <span class="meta-label">案情图谱</span>
        <span class="meta-value">{{ graph.query || '等待会话触发图谱生成' }}</span>
      </div>
      <div class="meta-block compact">
        <span class="meta-pill">{{ graph.nodes?.length || 0 }} 节点</span>
        <span class="meta-pill">{{ graph.edges?.length || 0 }} 关系</span>
      </div>
    </div>

    <div class="legend">
      <span v-for="item in legendItems" :key="item.category" class="legend-item">
        <span class="legend-dot" :style="{ background: item.color }"></span>
        {{ item.label }}
      </span>
    </div>

    <div class="graph-canvas">
      <div v-if="graph.loading" class="graph-overlay">
        <el-icon class="rotating"><Loading /></el-icon>
        <span>正在加载当前会话的案情图谱...</span>
      </div>
      <div v-else-if="graph.error" class="graph-overlay error">
        <el-icon><Warning /></el-icon>
        <span>{{ graph.error }}</span>
      </div>
      <div v-else-if="!layout.nodes.length" class="graph-overlay empty">
        <el-icon><Share /></el-icon>
        <span>当前会话尚未形成可视化案情图谱</span>
      </div>

      <svg
        v-else
        class="graph-svg"
        :viewBox="`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`"
        role="img"
        aria-label="动态案情图谱"
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
          {{ selectedNode.category || '未分类' }}
        </el-tag>
      </div>
      <div class="detail-row">
        <span class="detail-key">标签</span>
        <span class="detail-value">{{ (selectedNode.labels || []).join(', ') || '-' }}</span>
      </div>
      <div
        v-for="entry in selectedProperties"
        :key="entry.key"
        class="detail-row"
      >
        <span class="detail-key">{{ entry.key }}</span>
        <span class="detail-value">{{ entry.value }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

const VIEW_WIDTH = 520
const VIEW_HEIGHT = 440
const CATEGORY_ANCHORS = {
  主体: { x: 140, y: 120 },
  客体: { x: 380, y: 120 },
  事件: { x: 140, y: 320 },
  后果: { x: 380, y: 320 },
  未分类: { x: VIEW_WIDTH / 2, y: VIEW_HEIGHT / 2 },
}

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

const selectedNodeId = ref('')

const legendItems = [
  { category: '主体', label: '主体', color: '#2563eb' },
  { category: '客体', label: '客体', color: '#f59e0b' },
  { category: '事件', label: '事件', color: '#dc2626' },
  { category: '后果', label: '后果', color: '#16a34a' },
]

const layout = computed(() => buildForceLayout(props.graph.nodes || [], props.graph.edges || []))

const selectedNode = computed(() =>
  layout.value.nodes.find((node) => node.id === selectedNodeId.value) || layout.value.nodes[0] || null
)

const selectedProperties = computed(() => {
  if (!selectedNode.value?.properties) return []
  return Object.entries(selectedNode.value.properties)
    .filter(([, value]) => value !== null && value !== undefined && `${value}`.trim() !== '')
    .slice(0, 8)
    .map(([key, value]) => ({
      key,
      value: `${value}`.slice(0, 160),
    }))
})

watch(
  () => props.graph.nodes,
  (nodes) => {
    selectedNodeId.value = nodes?.[0]?.id || ''
  },
  { immediate: true }
)

function shortLabel(value) {
  if (!value) return ''
  return value.length > 8 ? `${value.slice(0, 8)}...` : value
}

function normalizeCategory(value) {
  if (typeof value !== 'string') return '未分类'
  return CATEGORY_ANCHORS[value] ? value : '未分类'
}

function colorForCategory(category) {
  const palette = {
    主体: '#2563eb',
    客体: '#f59e0b',
    事件: '#dc2626',
    后果: '#16a34a',
    未分类: '#7c3aed',
  }
  return palette[normalizeCategory(category)] || palette.未分类
}

function buildForceLayout(nodes, edges) {
  if (!nodes.length) {
    return { nodes: [], edges: [] }
  }

  const state = nodes.map((node, index) => {
    const category = normalizeCategory(node.category || node.type)
    const anchor = CATEGORY_ANCHORS[category]
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
  grid-template-columns: 52px 1fr;
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

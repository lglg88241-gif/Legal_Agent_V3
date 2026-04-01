<template>
  <div class="graph-panel">
    <div class="graph-meta">
      <div class="meta-block">
        <span class="meta-label">图谱查询</span>
        <span class="meta-value">{{ graph.query || '等待问题触发' }}</span>
      </div>
      <div class="meta-block compact">
        <span class="meta-pill">{{ graph.nodes?.length || 0 }} 节点</span>
        <span class="meta-pill">{{ graph.edges?.length || 0 }} 关系</span>
      </div>
    </div>

    <div class="legend">
      <span v-for="item in legendItems" :key="item.type" class="legend-item">
        <span class="legend-dot" :style="{ background: item.color }"></span>
        {{ item.label }}
      </span>
    </div>

    <div class="graph-canvas">
      <div v-if="graph.loading" class="graph-overlay">
        <el-icon class="rotating"><Loading /></el-icon>
        <span>正在加载 Neo4j 子图...</span>
      </div>
      <div v-else-if="graph.error" class="graph-overlay error">
        <el-icon><Warning /></el-icon>
        <span>{{ graph.error }}</span>
      </div>
      <div v-else-if="!layout.nodes.length" class="graph-overlay empty">
        <el-icon><Share /></el-icon>
        <span>当前问题尚未命中可视化图谱</span>
      </div>

      <svg
        v-else
        class="graph-svg"
        :viewBox="`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`"
        role="img"
        aria-label="知识图谱"
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
            :r="selectedNodeId === node.id ? 22 : 18"
            :fill="colorForType(node.type)"
            :class="['node-circle', { active: selectedNodeId === node.id }]"
          />
          <text :x="node.x" :y="node.y + 4" class="node-label">
            {{ shortLabel(node.label) }}
          </text>
        </g>
      </svg>
    </div>

    <div v-if="selectedNode" class="node-detail">
      <div class="detail-head">
        <span class="detail-title">{{ selectedNode.label }}</span>
        <el-tag size="small" effect="plain">{{ selectedNode.type }}</el-tag>
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

const VIEW_WIDTH = 320
const VIEW_HEIGHT = 320
const CENTER_X = VIEW_WIDTH / 2
const CENTER_Y = VIEW_HEIGHT / 2
const ANCHORS = {
  plaintiff: { x: 82, y: 92 },
  defendant: { x: 238, y: 92 },
  cause: { x: 88, y: 232 },
  judgment: { x: 232, y: 232 },
  law: { x: CENTER_X, y: 76 },
  entity: { x: CENTER_X, y: CENTER_Y },
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
  { type: 'plaintiff', label: '原告', color: '#2563eb' },
  { type: 'defendant', label: '被告', color: '#dc2626' },
  { type: 'cause', label: '案由', color: '#d97706' },
  { type: 'judgment', label: '判决', color: '#059669' },
  { type: 'entity', label: '实体', color: '#7c3aed' },
]

const layout = computed(() => buildForceLayout(props.graph.nodes || [], props.graph.edges || []))

const selectedNode = computed(() =>
  layout.value.nodes.find((node) => node.id === selectedNodeId.value) || layout.value.nodes[0] || null
)

const selectedProperties = computed(() => {
  if (!selectedNode.value?.properties) return []
  return Object.entries(selectedNode.value.properties)
    .filter(([, value]) => value !== null && value !== undefined && `${value}`.trim() !== '')
    .slice(0, 6)
    .map(([key, value]) => ({
      key,
      value: `${value}`.slice(0, 120),
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
  return value.length > 6 ? `${value.slice(0, 6)}…` : value
}

function colorForType(type) {
  const palette = {
    plaintiff: '#2563eb',
    defendant: '#dc2626',
    cause: '#d97706',
    judgment: '#059669',
    law: '#0f766e',
    entity: '#7c3aed',
  }
  return palette[type] || palette.entity
}

function buildForceLayout(nodes, edges) {
  if (!nodes.length) {
    return { nodes: [], edges: [] }
  }

  const typeIndexes = {}
  const state = nodes.map((node, index) => {
    const type = node.type || 'entity'
    const offset = typeIndexes[type] || 0
    typeIndexes[type] = offset + 1
    const anchor = ANCHORS[type] || ANCHORS.entity
    const angle = (Math.PI * 2 * index) / Math.max(nodes.length, 1)

    return {
      ...node,
      x: anchor.x + Math.cos(angle) * 18,
      y: anchor.y + Math.sin(angle) * 18,
      vx: 0,
      vy: 0,
      anchorX: anchor.x,
      anchorY: anchor.y,
      offset,
    }
  })

  const nodeMap = new Map(state.map((node) => [node.id, node]))
  const validEdges = edges.filter((edge) => nodeMap.has(edge.source) && nodeMap.has(edge.target))

  for (let step = 0; step < 140; step += 1) {
    for (let i = 0; i < state.length; i += 1) {
      for (let j = i + 1; j < state.length; j += 1) {
        const a = state[i]
        const b = state[j]
        const dx = a.x - b.x
        const dy = a.y - b.y
        const distance = Math.sqrt(dx * dx + dy * dy) || 1
        const force = 380 / (distance * distance)
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
      const force = (distance - 92) * 0.012
      const fx = (dx / distance) * force
      const fy = (dy / distance) * force
      source.vx += fx
      source.vy += fy
      target.vx -= fx
      target.vy -= fy
    }

    for (const node of state) {
      node.vx += (node.anchorX - node.x) * 0.018
      node.vy += (node.anchorY - node.y) * 0.018
      node.vx *= 0.84
      node.vy *= 0.84
      node.x = Math.min(VIEW_WIDTH - 30, Math.max(30, node.x + node.vx))
      node.y = Math.min(VIEW_HEIGHT - 30, Math.max(30, node.y + node.vy))
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
        labelY: (source.y + target.y) / 2 - 6,
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
  min-height: 320px;
  border-radius: 16px;
  overflow: hidden;
  background:
    radial-gradient(circle at top, rgba(59, 130, 246, 0.15), transparent 36%),
    linear-gradient(180deg, #f8fafc 0%, #eef4ff 100%);
  border: 1px solid #dbeafe;
}

.graph-svg {
  width: 100%;
  height: 320px;
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
  background: rgba(248, 250, 252, 0.9);
  z-index: 2;
}

.graph-overlay.error {
  color: #b42318;
}

.graph-overlay.empty {
  color: #64748b;
}

.edge-line {
  stroke: rgba(71, 85, 105, 0.4);
  stroke-width: 1.5;
}

.edge-label {
  text-anchor: middle;
  font-size: 9px;
  fill: #64748b;
}

.node-group {
  cursor: pointer;
}

.node-circle {
  stroke: rgba(255, 255, 255, 0.9);
  stroke-width: 2;
  transition: transform 0.2s ease, stroke 0.2s ease;
}

.node-circle.active {
  stroke: #0f172a;
}

.node-label {
  font-size: 9px;
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

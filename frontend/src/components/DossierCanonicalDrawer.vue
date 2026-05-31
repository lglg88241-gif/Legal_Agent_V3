<template>
  <div class="dossier-canonical-section">
    <div class="dossier-auto-summary-header">
      <div class="dossier-toolbar-copy">
        <div class="dossier-detail-conversations-title">案卷规范实体图</div>
        <span class="dossier-toolbar-subtitle">
          这里只展示案卷中的责任主体和关键主体关系，例如个人、公司、单位、机构及其代表关系。劳动合同、社保、金额、通知等事实信息会作为详情证据展示，不作为主节点。
        </span>
      </div>
      <el-button
        size="small"
        plain
        :loading="loading || intelligenceLoading"
        @click="emit('refresh-canonical')"
      >
        刷新规范实体图
      </el-button>
    </div>
    <div class="dossier-canonical-body">
      <div class="dossier-canonical-summary">
        <span class="dossier-canonical-chip">
          案卷实体 {{ stats.connectedEntities }}
        </span>
        <span class="dossier-canonical-chip" :class="{ urgent: highPriorityCrossConversationTasks.length }">
          高优先待确认 {{ highPriorityCrossConversationTasks.length }}
        </span>
        <span class="dossier-canonical-chip">
          一般候选 {{ regularCrossConversationTasks.length }}
        </span>
        <span class="dossier-canonical-chip">
          来源会话 {{ stats.boundConversations }}
        </span>
        <span class="dossier-canonical-chip">
          关系 {{ stats.relations }}
        </span>
      </div>

      <div
        v-if="hasSingleSourceHint"
        class="dossier-canonical-hint"
      >
        当前仅有一个来源会话，跨会话连接将在绑定更多会话后出现。
      </div>

      <div class="dossier-canonical-graph-shell">
        <KnowledgeGraphPanel
          :graph="graphPreview"
          @node-action="(node) => emit('open-graph-node-conversation', node)"
        />
      </div>

      <div
        v-if="confirmedEntities.length"
        class="dossier-canonical-group"
      >
        <div class="dossier-detail-conversations-title">已连接主体</div>
        <div class="dossier-canonical-entity-list dossier-canonical-entity-list-compact">
          <div
            v-for="entity in confirmedEntities"
            :key="entity.id"
            class="dossier-canonical-entity-row"
            :class="{ clickable: helpers.canonicalEntityHasDetailConversation(entity) }"
            @click="helpers.canonicalEntityHasDetailConversation(entity) && emit('open-entity-conversation', entity)"
          >
            <div class="dossier-canonical-entity-main">
              <div class="dossier-canonical-entity-title">
                <strong>{{ entity.label || '未命名主体' }}</strong>
                <span>{{ `${helpers.canonicalEntityConversationCount(entity)} 个来源会话` }}</span>
              </div>
              <div class="dossier-canonical-entity-meta">
                <span>{{ helpers.canonicalEntitySourceLabelText(entity) }}</span>
                <span>{{ `信息最多：${helpers.canonicalEntityDetailConversationLabel(entity)}` }}</span>
              </div>
              <div
                v-if="helpers.canonicalEntitySourceConversationBadges(entity).length"
                class="dossier-canonical-source-badges"
              >
                <span
                  v-for="conversation in helpers.canonicalEntitySourceConversationBadges(entity)"
                  :key="`${entity.id}-${conversation.id}`"
                  class="dossier-canonical-source-badge"
                  :class="{ best: conversation.isBest }"
                >
                  {{ conversation.label }}
                </span>
              </div>
            </div>
            <div class="dossier-canonical-entity-actions">
              <el-button
                size="small"
                type="primary"
                text
                :disabled="!helpers.canonicalEntityHasDetailConversation(entity)"
                @click.stop="emit('open-entity-conversation', entity)"
              >
                跳到信息最多
              </el-button>
              <el-button
                size="small"
                text
                :disabled="helpers.canonicalEntityConversationCount(entity) < 1 || confirmedEntities.length < 2"
                @click.stop="emit('open-manual-merge', entity)"
              >
                跨会话合并
              </el-button>
            </div>
          </div>
        </div>
      </div>

      <div
        v-if="highPriorityCrossConversationTasks.length"
        class="dossier-canonical-group dossier-canonical-group-priority"
      >
        <div class="dossier-canonical-group-heading">
          <div>
            <div class="dossier-detail-conversations-title">高优先待确认合并</div>
            <div class="dossier-canonical-group-subtitle">
              系统高度怀疑是同一实体，但仍需要人工确认后才归并。
            </div>
          </div>
        </div>
        <div class="dossier-intelligence-task-list">
          <div
            v-for="task in highPriorityCrossConversationTasks"
            :key="task.id || task.task_key"
            class="dossier-intelligence-task-card dossier-intelligence-task-card-priority"
          >
            <div class="dossier-intelligence-task-header">
              <strong>{{ helpers.canonicalTaskPairTitle(task) }}</strong>
              <span class="dossier-intelligence-task-type">
                {{ helpers.canonicalTaskConfidenceLabel(task) }}置信
              </span>
            </div>
            <div class="dossier-intelligence-task-reason">
              {{ task.reason || '不同会话出现相同或重叠主体称呼，需要确认是否为同一案卷实体。' }}
            </div>
            <div class="dossier-intelligence-task-meta">
              <span v-if="helpers.canonicalTaskScoreText(task)">{{ helpers.canonicalTaskScoreText(task) }}</span>
              <span v-if="task.auto_merge_recommended">满足高置信合并条件，仍等待人工确认</span>
              <span>{{ `来源会话：${helpers.formatGraphConversationListLabel(task.source_conversation_ids || [])}` }}</span>
            </div>
            <div class="dossier-intelligence-task-evidence">
              <span>{{ `来源实体：${helpers.canonicalTaskEntitySummary(task.source_entity)}` }}</span>
              <span>{{ `候选实体：${helpers.canonicalTaskEntitySummary(task.target_entity)}` }}</span>
            </div>
            <div class="dossier-intelligence-task-actions dossier-intelligence-task-actions-stacked">
              <div class="dossier-inline-actions dossier-inline-actions-wrap">
                <el-button
                  size="small"
                  type="primary"
                  :loading="helpers.canonicalTaskIsSubmitting(task)"
                  :disabled="helpers.canonicalTaskIsSubmitting(task)"
                  @click="emit('confirm-cross-conversation-same-entity', task)"
                >
                  标记为同一实体
                </el-button>
                <el-button
                  size="small"
                  :disabled="helpers.canonicalTaskIsSubmitting(task)"
                  @click="emit('reject-cross-conversation-same-entity', task)"
                >
                  不是同一实体
                </el-button>
                <el-button
                  size="small"
                  text
                  :disabled="helpers.canonicalTaskIsSubmitting(task)"
                  @click="emit('open-manual-merge', task.source_entity)"
                >
                  手动选择目标实体
                </el-button>
                <el-button
                  size="small"
                  text
                  :disabled="helpers.canonicalTaskIsSubmitting(task)"
                  @click="emit('open-entity-conversation', task.source_entity)"
                >
                  查看来源会话
                </el-button>
              </div>
              <span>确认后会刷新案卷图谱与智能概览。</span>
            </div>
          </div>
        </div>
      </div>

      <div
        v-if="regularCrossConversationTasks.length"
        class="dossier-canonical-group"
      >
        <div class="dossier-canonical-group-heading">
          <div>
            <div class="dossier-detail-conversations-title">一般跨会话候选确认</div>
            <div class="dossier-canonical-group-subtitle">
              相似主体需要人工判断，可确认、排除，或改用手动目标选择。
            </div>
          </div>
        </div>
        <div class="dossier-intelligence-task-list">
          <div
            v-for="task in regularCrossConversationTasks"
            :key="task.id || task.task_key"
            class="dossier-intelligence-task-card"
          >
            <div class="dossier-intelligence-task-header">
              <strong>{{ helpers.canonicalTaskPairTitle(task) }}</strong>
              <span class="dossier-intelligence-task-type">
                {{ helpers.canonicalTaskConfidenceLabel(task) }}置信
              </span>
            </div>
            <div class="dossier-intelligence-task-reason">
              {{ task.reason || '不同会话出现相同或重叠主体称呼，需要确认是否为同一案卷实体。' }}
            </div>
            <div class="dossier-intelligence-task-meta">
              <span v-if="helpers.canonicalTaskScoreText(task)">{{ helpers.canonicalTaskScoreText(task) }}</span>
              <span>{{ `来源会话：${helpers.formatGraphConversationListLabel(task.source_conversation_ids || [])}` }}</span>
            </div>
            <div class="dossier-intelligence-task-evidence">
              <span>{{ `来源实体：${helpers.canonicalTaskEntitySummary(task.source_entity)}` }}</span>
              <span>{{ `候选实体：${helpers.canonicalTaskEntitySummary(task.target_entity)}` }}</span>
            </div>
            <div class="dossier-intelligence-task-actions dossier-intelligence-task-actions-stacked">
              <div class="dossier-inline-actions dossier-inline-actions-wrap">
                <el-button
                  size="small"
                  type="primary"
                  :loading="helpers.canonicalTaskIsSubmitting(task)"
                  :disabled="helpers.canonicalTaskIsSubmitting(task)"
                  @click="emit('confirm-cross-conversation-same-entity', task)"
                >
                  标记为同一实体
                </el-button>
                <el-button
                  size="small"
                  :disabled="helpers.canonicalTaskIsSubmitting(task)"
                  @click="emit('reject-cross-conversation-same-entity', task)"
                >
                  不是同一实体
                </el-button>
                <el-button
                  size="small"
                  text
                  :disabled="helpers.canonicalTaskIsSubmitting(task)"
                  @click="emit('open-manual-merge', task.source_entity)"
                >
                  手动选择目标实体
                </el-button>
                <el-button
                  size="small"
                  text
                  :disabled="helpers.canonicalTaskIsSubmitting(task)"
                  @click="emit('open-entity-conversation', task.source_entity)"
                >
                  查看来源会话
                </el-button>
              </div>
              <span>确认后会刷新案卷图谱与智能概览。</span>
            </div>
          </div>
        </div>
      </div>

    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import KnowledgeGraphPanel from './KnowledgeGraphPanel.vue'

const props = defineProps({
  loading: { type: Boolean, default: false },
  intelligenceLoading: { type: Boolean, default: false },
  graphPreview: { type: Object, default: () => ({}) },
  stats: { type: Object, default: () => ({}) },
  hasSingleSourceHint: { type: Boolean, default: false },
  confirmedEntities: { type: Array, default: () => [] },
  crossConversationTasks: { type: Array, default: () => [] },
  helpers: { type: Object, default: () => ({}) },
})

const emit = defineEmits([
  'refresh-canonical',
  'open-graph-node-conversation',
  'open-entity-conversation',
  'open-manual-merge',
  'confirm-cross-conversation-same-entity',
  'reject-cross-conversation-same-entity',
])

function isHighPriorityCrossConversationTask(task) {
  const confidence = String(task?.confidence || '').trim().toLowerCase()
  const score = Number(task?.score)
  return (
    Boolean(task?.high_priority || task?.priority === 'high' || task?.auto_merge_recommended) ||
    confidence === 'high' ||
    (Number.isFinite(score) && score >= 0.92)
  )
}

const highPriorityCrossConversationTasks = computed(() =>
  props.crossConversationTasks.filter((task) => isHighPriorityCrossConversationTask(task))
)

const regularCrossConversationTasks = computed(() =>
  props.crossConversationTasks.filter((task) => !isHighPriorityCrossConversationTask(task))
)
</script>

<style scoped>
.dossier-canonical-section {
  padding: 12px;
  border: 1px solid #dbeafe;
  border-radius: 12px;
  background: linear-gradient(180deg, #f8fbff, #f3f7ff);
}

.dossier-canonical-body {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.dossier-canonical-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.dossier-canonical-hint {
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.9);
  border: 1px dashed #bfdbfe;
  color: #475569;
  font-size: 12px;
  line-height: 1.7;
}

.dossier-canonical-graph-shell {
  padding: 12px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid #dbeafe;
}

.dossier-canonical-chip {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 0 10px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid #bfdbfe;
  color: #1d4ed8;
  font-size: 12px;
  font-weight: 600;
}

.dossier-canonical-chip.urgent {
  border-color: #f97316;
  background: #fff7ed;
  color: #c2410c;
}

.dossier-canonical-group {
  padding: 12px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid #dbeafe;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dossier-canonical-group-priority {
  border-color: #fed7aa;
  background: #fffaf5;
}

.dossier-canonical-group-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.dossier-canonical-group-subtitle {
  margin-top: -4px;
  font-size: 12px;
  line-height: 1.5;
  color: #6b7280;
}

.dossier-canonical-entity-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dossier-canonical-entity-list-compact {
  gap: 6px;
}

.dossier-canonical-entity-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-height: 48px;
  padding: 8px 10px;
  border-radius: 8px;
  background: #f8fbff;
  border: 1px solid #dbeafe;
}

.dossier-canonical-entity-row.clickable {
  cursor: pointer;
}

.dossier-canonical-entity-row.clickable:hover {
  border-color: #93c5fd;
  background: #eff6ff;
}

.dossier-canonical-entity-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.dossier-canonical-entity-title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.dossier-canonical-entity-title strong {
  font-size: 13px;
  color: #111827;
}

.dossier-canonical-entity-title span,
.dossier-canonical-entity-meta {
  font-size: 12px;
  color: #6b7280;
}

.dossier-canonical-entity-meta {
  display: flex;
  gap: 8px;
  line-height: 1.4;
  min-width: 0;
}

.dossier-canonical-entity-meta span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dossier-canonical-source-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.dossier-canonical-source-badge {
  display: inline-flex;
  align-items: center;
  max-width: 160px;
  min-height: 20px;
  padding: 0 6px;
  border-radius: 999px;
  border: 1px solid #d1d5db;
  background: #fff;
  color: #4b5563;
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dossier-canonical-source-badge.best {
  border-color: #93c5fd;
  background: #eff6ff;
  color: #1d4ed8;
  font-weight: 600;
}

.dossier-canonical-entity-actions {
  display: inline-flex;
  align-items: center;
  flex: 0 0 auto;
}

.dossier-canonical-empty {
  padding: 12px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.82);
  border: 1px dashed #bfdbfe;
  color: #6b7280;
  font-size: 12px;
  line-height: 1.7;
}

.dossier-intelligence-task-list {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dossier-intelligence-task-card {
  padding: 10px;
  border-radius: 8px;
  border: 1px solid #bfdbfe;
  background: #f8fbff;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.dossier-intelligence-task-card-priority {
  border-color: #fdba74;
  background: #fff7ed;
}

.dossier-intelligence-task-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.dossier-intelligence-task-header strong {
  font-size: 13px;
  color: #111827;
}

.dossier-intelligence-task-type {
  font-size: 11px;
  color: #1d4ed8;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  border-radius: 999px;
  padding: 4px 8px;
}

.dossier-intelligence-task-meta,
.dossier-intelligence-task-evidence {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: #4b5563;
  line-height: 1.6;
}

.dossier-intelligence-task-reason {
  font-size: 12px;
  color: #1f2937;
  line-height: 1.7;
}

.dossier-intelligence-task-input {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.dossier-intelligence-task-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 12px;
  color: #6b7280;
}

@media (max-width: 900px) {
  .dossier-canonical-entity-row {
    flex-direction: column;
    align-items: stretch;
  }

  .dossier-canonical-entity-actions {
    width: 100%;
    justify-content: flex-start;
  }

  .dossier-canonical-entity-meta {
    flex-direction: column;
    gap: 4px;
  }
}
</style>

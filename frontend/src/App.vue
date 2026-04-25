<template>
  <div class="system-banner" :class="systemStatus.ready ? 'ready' : 'loading'">
    <div class="system-banner-content">
      <div class="system-banner-main">
        <el-icon v-if="systemStatus.ready" class="banner-icon"><CircleCheckFilled /></el-icon>
        <el-icon v-else class="banner-icon rotating"><Loading /></el-icon>
        <span>{{ systemStatus.ready ? '系统已就绪' : '系统核心组件初始化中...' }}</span>
      </div>
      <div class="system-banner-meta">
        <span>Chroma {{ componentLabel(systemStatus.components.chroma) }}</span>
        <span>Neo4j {{ componentLabel(systemStatus.components.neo4j) }}</span>
        <span>云端 LLM {{ componentLabel(systemStatus.components.llm) }}</span>
        <span>Celery {{ componentLabel(systemStatus.components.celery) }}</span>
      </div>
    </div>
  </div>

  <el-container class="app-container">
    <el-header class="app-header">
      <div class="header-left">
        <el-icon :size="22"><Promotion /></el-icon>
        <span class="app-title">法律智能工作台</span>
      </div>
      <div class="header-right">
        <span class="user-chip">{{ currentUsername }}</span>
        <el-button text @click="openDossierDrawer">
          案卷
        </el-button>
        <el-button
          text
          @click="handleSummary"
          :loading="summaryLoading"
          :disabled="!systemStatus.ready || !currentConversationId"
        >
          <el-icon><Document /></el-icon>总结摘要
        </el-button>
        <el-button text @click="handleExport" :disabled="!currentConversationId">
          <el-icon><Download /></el-icon>导出
        </el-button>
        <el-button
          class="clear-reset-button"
          text
          type="danger"
          @click="handleClearReset"
          :disabled="!currentConversationId"
        >
          <el-icon><Delete /></el-icon>清空聊天并重置案情
        </el-button>
        <el-button text type="danger" @click="handleLogout">退出登录</el-button>
      </div>
    </el-header>

    <el-container class="main-area">
      <el-aside width="260px" class="side-panel left-panel">
        <SessionList
          :sessions="conversations"
          :current-id="currentConversationId"
          :active-summary="currentConversation?.case_summary || ''"
          :loading="conversationListLoading"
          :search-keyword="conversationSearchKeyword"
          :batch-mode="sessionBatchMode"
          :empty-text="conversationListEmptyText"
          @create="createNewConversation"
          @switch="switchConversation"
          @rename="promptRenameConversation"
          @delete="removeConversationV2"
          @search="handleConversationSearch"
          @batch-mode-change="handleSessionBatchModeChange"
          @batch-delete="handleBatchDeleteV2"
        />
      </el-aside>

      <el-main class="chat-area">
        <div class="local-model-card">
          <div class="local-model-header">
            <div class="local-model-title">本地模型加速</div>
            <el-switch
              v-model="localModel.enabled"
              :loading="localModel.loading"
              :disabled="localModel.loading"
              @change="handleLocalModelToggle"
            />
          </div>
          <div class="local-model-status" :class="localModelStatusClass">
            {{ localModelStatusText }}
          </div>
        </div>

        <div v-if="currentConversation" class="conversation-mode-card">
          <div class="conversation-mode-main">
            <div class="conversation-mode-copy">
              <span class="conversation-mode-title">当前模式</span>
              <span class="conversation-mode-subtitle">
                {{ currentConversation.title || `会话 #${currentConversation.id}` }}
              </span>
            </div>
            <div class="conversation-mode-tags">
              <el-tag size="small" effect="light" :type="currentConversationModeTagType">
                {{ currentConversationModeLabel }}
              </el-tag>
              <el-tag
                v-if="currentConversation.case_type"
                size="small"
                effect="plain"
              >
                {{ currentConversation.case_type }}
              </el-tag>
              <el-tag
                v-if="currentConversation.dossier_id"
                size="small"
                effect="plain"
                type="primary"
              >
                {{ currentConversationDossierLabel }}
              </el-tag>
            </div>
          </div>
          <div class="conversation-dossier-row">
            <div class="conversation-dossier-copy">
              <span class="conversation-dossier-title">当前案卷</span>
              <span class="conversation-dossier-subtitle">
                {{
                  currentConversation.dossier_id
                    ? `已绑定 ${currentConversationDossierLabel}`
                    : '当前会话尚未绑定案卷'
                }}
              </span>
            </div>
            <el-button size="small" plain @click="openDossierDrawer">
              {{ currentConversation.dossier_id ? '更换案卷' : '绑定案卷' }}
            </el-button>
          </div>
        </div>

        <div v-if="!systemStatus.ready" class="init-overlay">
          <div class="init-card">
            <el-icon class="init-icon rotating"><Loading /></el-icon>
            <div class="init-title">系统核心组件初始化中...</div>
            <div class="init-subtitle">
              正在预热 Chroma、Neo4j、本地模型与 Celery，输入框将在全部组件可用后自动解锁。
            </div>
          </div>
        </div>

        <el-scrollbar ref="scrollRef" class="chat-scroll">
          <div class="message-list" ref="messageListRef">
            <div v-if="!messages.length" class="empty-state">
              <div class="empty-title">发起新的法律咨询</div>
              <div class="empty-subtitle">
                从左侧选择历史会话，或创建一个新会话后开始提问。
              </div>
            </div>

            <ChatMessage v-for="message in messages" :key="message.id || message.key" :msg="message" />

            <div v-if="sending" class="chat-message bot-msg loading-msg">
              <div class="avatar">
                <el-avatar :size="36" style="background: #67c23a">
                  <el-icon><Service /></el-icon>
                </el-avatar>
              </div>
              <div class="bubble-wrap">
                <div class="bubble loading-bubble">
                  <span class="dot"></span><span class="dot"></span><span class="dot"></span>
                </div>
              </div>
            </div>
          </div>
        </el-scrollbar>

        <div class="input-area" :class="{ locked: !systemStatus.ready }">
          <div class="input-stack">
            <div class="mode-switch-row">
              <span class="mode-switch-label">精准案情梳理</span>
              <el-switch
                v-model="proactiveGraphMode"
                :disabled="sending || !currentConversationId"
                inline-prompt
                active-text="开"
                inactive-text="关"
              />
            </div>
            <el-input
              v-model="inputText"
              type="textarea"
              :rows="2"
              :disabled="!systemStatus.ready || sending || !currentConversationId"
              :placeholder="inputPlaceholder"
              resize="none"
              @keydown="handleKeydown"
            />
          </div>
          <el-button
            type="primary"
            @click="() => sendImpl()"
            :loading="sending"
            :disabled="!systemStatus.ready || !inputText.trim() || !currentConversationId"
          >
            发送
          </el-button>
        </div>
      </el-main>

      <el-aside
        :width="graphSidebarCollapsed ? '72px' : '360px'"
        class="side-panel right-panel"
        :class="{ collapsed: graphSidebarCollapsed }"
      >
        <AnalysisPanel
          :status="systemStatus"
          :analysis="currentAnalysis"
          :graph="currentGraph"
          :collapsed="graphSidebarCollapsed"
          @feedback="handleFeedback"
          @toggle-sidebar="graphSidebarCollapsed = !graphSidebarCollapsed"
        />
      </el-aside>
    </el-container>
  </el-container>

  <el-drawer
    v-model="dossierDrawerVisible"
    title="案卷"
    size="420px"
    destroy-on-close
  >
    <div class="dossier-drawer">
      <div class="dossier-toolbar">
        <div class="dossier-toolbar-copy">
          <span class="dossier-toolbar-title">案卷管理</span>
          <span class="dossier-toolbar-subtitle">创建案卷并绑定当前会话</span>
        </div>
        <el-button size="small" plain @click="refreshDossiers()" :loading="dossierListLoading">
          刷新
        </el-button>
      </div>

      <div class="dossier-current-card">
        <div class="dossier-current-title">当前会话</div>
        <div class="dossier-current-name">
          {{ currentConversation?.title || '未选择会话' }}
        </div>
        <div class="dossier-current-hint">
          {{
            currentConversation?.dossier_id
              ? `已绑定 ${currentConversationDossierLabel}`
              : '可先创建案卷，再将当前会话绑定到案卷'
          }}
        </div>
      </div>

      <div class="dossier-create-card">
        <div class="dossier-section-title">新建案卷</div>
        <el-input
          v-model="dossierForm.title"
          maxlength="255"
          placeholder="案卷标题"
        />
        <el-input
          v-model="dossierForm.case_type"
          maxlength="100"
          placeholder="案由 / 类型，例如劳动争议"
        />
        <el-input
          v-model="dossierForm.summary"
          type="textarea"
          :rows="3"
          maxlength="5000"
          show-word-limit
          resize="none"
          placeholder="案卷摘要（可选）"
        />
        <el-button type="primary" :loading="dossierSaving" @click="handleCreateDossier">
          创建案卷
        </el-button>
      </div>

      <div class="dossier-list-card">
        <div class="dossier-section-title">我的案卷</div>
        <div v-if="dossierListLoading" class="dossier-list-status">正在加载案卷...</div>
        <el-empty
          v-else-if="!dossiers.length"
          description="暂无案卷，可先创建一个"
          :image-size="64"
        />
        <div v-else class="dossier-list">
          <div
            v-for="dossier in dossiers"
            :key="dossier.id"
            class="dossier-item"
            :class="{ active: dossier.id === selectedDossierId }"
            @click="handleSelectDossier(dossier)"
          >
            <div class="dossier-item-main">
              <div class="dossier-item-copy">
                <div class="dossier-item-title">
                  {{ dossier.title || `案卷 #${dossier.id}` }}
                </div>
                <div class="dossier-item-meta">
                  <el-tag size="small" effect="plain" :type="dossierStatusTagType(dossier.status)">
                    {{ dossierStatusLabel(dossier.status) }}
                  </el-tag>
                  <span v-if="dossier.case_type">{{ dossier.case_type }}</span>
                  <span v-else>未分类</span>
                </div>
                <div class="dossier-item-summary">
                  {{ dossier.summary || '暂无案卷摘要' }}
                </div>
              </div>
              <el-button
                size="small"
                type="primary"
                plain
                :disabled="!currentConversationId || dossierBindingId === dossier.id || dossier.id === currentConversation?.dossier_id"
                :loading="dossierBindingId === dossier.id"
                @click.stop="handleAttachDossier(dossier)"
              >
                {{ dossier.id === currentConversation?.dossier_id ? '已绑定当前会话' : '绑定当前会话' }}
              </el-button>
            </div>
          </div>
        </div>
      </div>

      <div class="dossier-detail-card">
        <div class="dossier-detail-header">
          <div class="dossier-toolbar-copy">
            <span class="dossier-section-title">案卷详情</span>
            <span class="dossier-toolbar-subtitle">查看案卷摘要并编辑基本信息</span>
          </div>
          <el-button
            v-if="selectedDossierId"
            size="small"
            type="primary"
            plain
            @click="handleCreateConversationFromSelectedDossier"
          >
            基于此案卷新建会话
          </el-button>
          <el-button
            v-if="selectedDossierId"
            size="small"
            plain
            @click="loadSelectedDossierDetail(selectedDossierId)"
            :loading="dossierDetailLoading"
          >
            刷新详情
          </el-button>
        </div>

        <div v-if="dossierDetailLoading" class="dossier-list-status">正在加载案卷详情...</div>
        <el-empty
          v-else-if="!selectedDossierDetail"
          description="请先从上方选择一个案卷"
          :image-size="64"
        />
        <div v-else class="dossier-detail-body">
          <div class="dossier-detail-meta">
            <el-tag size="small" effect="plain" :type="dossierStatusTagType(selectedDossierDetail.status)">
              {{ dossierStatusLabel(selectedDossierDetail.status) }}
            </el-tag>
            <span>{{ `绑定会话 ${selectedDossierConversationCount} 个` }}</span>
            <span>{{ `ID #${selectedDossierDetail.id}` }}</span>
          </div>

          <el-input
            v-model="dossierDetailForm.title"
            maxlength="255"
            placeholder="案卷标题"
          />
          <el-input
            v-model="dossierDetailForm.case_type"
            maxlength="100"
            placeholder="案由 / 类型"
          />
          <div class="dossier-detail-conversations-title">手动摘要</div>
          <el-input
            v-model="dossierDetailForm.summary"
            type="textarea"
            :rows="4"
            maxlength="5000"
            show-word-limit
            resize="none"
            placeholder="案卷摘要"
          />
          <el-select v-model="dossierDetailForm.status" placeholder="状态">
            <el-option label="进行中" value="active" />
            <el-option label="已归档" value="archived" />
            <el-option label="已关闭" value="closed" />
          </el-select>
          <el-button
            type="primary"
            :loading="dossierDetailSaving"
            @click="handleUpdateDossierDetail"
          >
            保存案卷信息
          </el-button>

          <div class="dossier-auto-summary-section">
            <div class="dossier-auto-summary-header">
              <div class="dossier-detail-conversations-title">智能摘要</div>
              <el-button
                size="small"
                type="primary"
                plain
                :loading="dossierSummaryGenerating"
                @click="handleGenerateDossierSummary"
              >
                {{ selectedDossierDetail.auto_summary ? '刷新智能摘要' : '智能生成摘要' }}
              </el-button>
            </div>
            <div
              v-if="selectedDossierDetail.auto_summary_updated_at || selectedDossierDetail.auto_summary_source_count"
              class="dossier-auto-summary-meta"
            >
              <span v-if="selectedDossierDetail.auto_summary_updated_at">
                {{ `最近生成：${formatDossierConversationTime(selectedDossierDetail.auto_summary_updated_at)}` }}
              </span>
              <span>{{ `来源会话：${selectedDossierDetail.auto_summary_source_count || 0}` }}</span>
            </div>
            <div class="dossier-auto-summary-content">
              {{ selectedDossierDetail.auto_summary || '暂无智能摘要，可基于已绑定会话生成。' }}
            </div>
          </div>

          <div class="dossier-participants-section">
            <div class="dossier-auto-summary-header">
              <div class="dossier-detail-conversations-title">当事人称呼</div>
              <el-button
                size="small"
                type="primary"
                plain
                :loading="dossierParticipantsSaving"
                @click="handleUpdateDossierParticipants"
              >
                保存当事人称呼
              </el-button>
            </div>
            <div class="dossier-participants-hint">
              这些称呼是用户确认的案卷别名，可使用化名或角色名，不等同于真实姓名。
            </div>
            <div class="dossier-participants-grid">
              <el-input
                v-model="dossierParticipantForm.primary_label"
                maxlength="100"
                placeholder="主要当事人称呼，例如：我"
              />
              <el-input
                v-model="dossierParticipantForm.primary_role"
                maxlength="100"
                placeholder="主要当事人角色，例如：劳动者"
              />
              <el-input
                v-model="dossierParticipantForm.opposing_label"
                maxlength="100"
                placeholder="对方称呼，例如：公司"
              />
              <el-input
                v-model="dossierParticipantForm.opposing_role"
                maxlength="100"
                placeholder="对方角色，例如：用人单位"
              />
            </div>
          </div>

          <div class="dossier-detail-conversations">
            <div class="dossier-detail-conversations-title">绑定会话</div>
            <el-empty
              v-if="!selectedDossierDetail.conversations?.length"
              description="当前案卷还没有绑定会话"
              :image-size="52"
            />
            <div v-else class="dossier-conversation-list">
              <div
                v-for="conversation in selectedDossierDetail.conversations"
                :key="conversation.id"
                class="dossier-conversation-item"
              >
                <div class="dossier-conversation-top">
                  <span class="dossier-conversation-title">
                    {{ conversation.title || `会话 #${conversation.id}` }}
                  </span>
                  <span class="dossier-conversation-time">
                    {{ formatDossierConversationTime(conversation.updated_at) }}
                  </span>
                </div>
                <div class="dossier-conversation-tags">
                  <el-tag size="small" effect="light" :type="conversationModeTagType(conversation.conversation_mode)">
                    {{ conversationModeLabel(conversation.conversation_mode) }}
                  </el-tag>
                  <el-tag size="small" effect="plain">
                    {{ persistenceConsentLabel(conversation.persistence_consent) }}
                  </el-tag>
                  <el-tag v-if="conversation.case_type" size="small" effect="plain">
                    {{ conversation.case_type }}
                  </el-tag>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </el-drawer>

  <el-dialog
    v-model="trackedOnboardingVisible"
    title="案卷初始化"
    width="520px"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    :show-close="false"
  >
    <div class="tracked-onboarding-copy">
      <div class="tracked-onboarding-title">为了后续持续跟踪，请确认案卷和当事人称呼。</div>
      <div class="tracked-onboarding-subtitle">
        你可以使用化名或角色名，不需要填写真实姓名。
      </div>
      <div class="tracked-onboarding-subtitle">
        当前聊天记录仍会保存在会话历史中；选择持续跟踪后，系统会把案情整理为可持续维护的结构化案情。
      </div>
    </div>

    <div class="tracked-onboarding-form">
      <el-input
        v-model="trackedOnboardingForm.title"
        maxlength="255"
        placeholder="案卷名称"
      />
      <div class="tracked-onboarding-grid">
        <el-input
          v-model="trackedOnboardingForm.primary_label"
          maxlength="100"
          placeholder="主要当事人称呼，例如：我"
        />
        <el-input
          v-model="trackedOnboardingForm.primary_role"
          maxlength="100"
          placeholder="主要当事人角色，例如：劳动者"
        />
        <el-input
          v-model="trackedOnboardingForm.opposing_label"
          maxlength="100"
          placeholder="对方称呼，例如：公司"
        />
        <el-input
          v-model="trackedOnboardingForm.opposing_role"
          maxlength="100"
          placeholder="对方角色，例如：用人单位"
        />
      </div>
      <div class="tracked-onboarding-hint">
        {{
          trackedOnboardingState.dossierId
            ? '当前会话已绑定案卷，本次会更新该案卷的名称和当事人称呼。'
            : '当前会为这次持续跟踪创建一个新案卷，并把本会话绑定到该案卷。'
        }}
      </div>
    </div>

    <template #footer>
      <div class="tracked-onboarding-actions">
        <el-button @click="handleCancelTrackedOnboarding" :disabled="trackedOnboardingSubmitting">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="trackedOnboardingSubmitting"
          @click="handleConfirmTrackedOnboarding"
        >
          确认并继续
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  CircleCheckFilled,
  Delete,
  Document,
  Download,
  Loading,
  Promotion,
  Service,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import AnalysisPanel from './components/AnalysisPanel.vue'
import ChatMessage from './components/ChatMessage.vue'
import SessionList from './components/SessionList.vue'
import {
  attachConversationToDossier,
  batchDeleteConversations,
  clearConversationMessages,
  createConversation,
  createDossier,
  deleteConversation,
  generateDossierSummary,
  getConversationGraph,
  getConversationMessages,
  getConversationSummary,
  getConversations,
  getDossierDetail,
  getDossiers,
  getLocalModelStatus,
  searchConversations,
  getSystemStatus,
  sendMessage,
  setConversationModeConsent,
  setLocalModelStatus,
  submitFeedback,
  updateDossier,
  updateDossierParticipants,
  updateConversationTitle,
} from './api/index.js'
import { clearAuthSession, getStoredUser } from './utils/auth.js'

const router = useRouter()

const conversations = ref([])
const currentConversationId = ref(null)
const currentSessionId = ref('')
const messages = ref([])
const inputText = ref('')
const proactiveGraphMode = ref(true)
const sending = ref(false)
const summaryLoading = ref(false)
const scrollRef = ref(null)
const messageListRef = ref(null)
const statusTimer = ref(null)
const graphRefreshTimer = ref(null)
const graphRefreshStopTimer = ref(null)
const graphSidebarCollapsed = ref(false)
const latestGraphRequestId = ref(0)
const conversationListLoading = ref(false)
const conversationSearchKeyword = ref('')
const sessionBatchMode = ref(false)
const dossiers = ref([])
const dossierDrawerVisible = ref(false)
const dossierListLoading = ref(false)
const dossierSaving = ref(false)
const dossierBindingId = ref(null)
const selectedDossierId = ref(null)
const selectedDossierDetail = ref(null)
const dossierDetailLoading = ref(false)
const dossierDetailSaving = ref(false)
const dossierParticipantsSaving = ref(false)
const dossierSummaryGenerating = ref(false)
const trackedOnboardingVisible = ref(false)
const trackedOnboardingSubmitting = ref(false)
const dossierForm = reactive({
  title: '',
  case_type: '',
  summary: '',
})
const dossierDetailForm = reactive({
  title: '',
  case_type: '',
  summary: '',
  status: 'active',
})
const dossierParticipantForm = reactive({
  primary_label: '',
  primary_role: '',
  opposing_label: '',
  opposing_role: '',
})
const trackedOnboardingForm = reactive({
  title: '',
  primary_label: '',
  primary_role: '',
  opposing_label: '',
  opposing_role: '',
})
const trackedOnboardingState = reactive({
  conversationId: null,
  text: '',
  forceCurrentCase: false,
  draftText: '',
  dossierId: null,
})
let trackedOnboardingResolver = null
const localModel = reactive({
  enabled: false,
  healthy: false,
  vllmUrl: '',
  loading: false,
})

const systemStatus = reactive({
  status: 'loading',
  ready: false,
  healthy: false,
  ragReady: false,
  docCount: 0,
  model: '',
  tools: [],
  components: {
    chroma: 'loading',
    neo4j: 'loading',
    llm: 'loading',
    celery: 'loading',
  },
})

const currentAnalysis = reactive({ actions: [], sources: [] })
const currentGraph = reactive({
  query: '',
  nodes: [],
  edges: [],
  tokens: [],
  generatedAt: '',
  loading: false,
  error: '',
})

const currentUsername = computed(() => getStoredUser()?.username || '已登录用户')
const currentConversation = computed(() =>
  conversations.value.find((item) => item.id === currentConversationId.value) || null
)
const currentConversationModeLabel = computed(() =>
  conversationModeLabel(currentConversation.value?.conversation_mode)
)
const currentConversationModeTagType = computed(() =>
  conversationModeTagType(currentConversation.value?.conversation_mode)
)
const currentConversationDossier = computed(() => {
  const dossierId = currentConversation.value?.dossier_id
  if (!dossierId) return null
  return (
    dossiers.value.find((item) => item.id === dossierId) || {
      id: dossierId,
      title: `案卷 #${dossierId}`,
      case_type: '',
      summary: '',
      status: 'active',
    }
  )
})
const currentConversationDossierLabel = computed(() => {
  const dossier = currentConversationDossier.value
  if (!dossier) return ''
  return dossier.title || `案卷 #${dossier.id}`
})
const selectedDossierConversationCount = computed(
  () => selectedDossierDetail.value?.conversation_count || 0
)
const conversationListEmptyText = computed(() =>
  conversationSearchKeyword.value.trim() ? '没有找到相关会话' : '暂无会话'
)
const localModelStatusText = computed(() => {
  if (localModel.loading) return '检查中...'
  if (!localModel.enabled) return '关闭（查询扩展走云端）'
  if (localModel.healthy) return `✅ 已连接 (${localModel.vllmUrl || '127.0.0.1:8001'})`
  return '❌ 连接失败'
})
const localModelStatusClass = computed(() => {
  if (!localModel.enabled) return 'is-off'
  return localModel.healthy ? 'is-on' : 'is-error'
})

const inputPlaceholder = computed(() => {
  if (!systemStatus.ready) return '系统初始化中，输入框暂时锁定。'
  if (!currentConversationId.value) return '请先创建或选择一个会话。'
  return '请输入法律问题，Enter 发送，Shift + Enter 换行。'
})

onMounted(async () => {
  startSystemStatusPolling()
  await refreshLocalModelStatus()
  await initializeConversations()
})

onUnmounted(() => {
  stopSystemStatusPolling()
  stopGraphRefreshPolling()
})

function componentLabel(value) {
  return value === 'connected' ? '已连接' : '加载中'
}

async function refreshLocalModelStatus() {
  try {
    const { data } = await getLocalModelStatus()
    localModel.enabled = Boolean(data.enabled)
    localModel.healthy = Boolean(data.vllm_healthy)
    localModel.vllmUrl = data.vllm_url || '127.0.0.1:8001'
  } catch (error) {
    console.error('Failed to fetch local model status:', error)
    localModel.enabled = false
    localModel.healthy = false
    localModel.vllmUrl = '127.0.0.1:8001'
  }
}

async function handleLocalModelToggle(enabled) {
  localModel.loading = true
  try {
    const { data } = await setLocalModelStatus(Boolean(enabled))
    if (!data.success) {
      localModel.enabled = false
      localModel.healthy = false
      ElMessage.warning(data.message || '请先启动 vLLM 服务')
      return
    }
    await refreshLocalModelStatus()
    if (localModel.enabled && !localModel.healthy) {
      localModel.enabled = false
      ElMessage.warning('请先启动 vLLM 服务')
    }
  } catch (error) {
    console.error('Failed to toggle local model:', error)
    localModel.enabled = false
    localModel.healthy = false
    ElMessage.error('本地模型设置失败')
  } finally {
    localModel.loading = false
  }
}

function sortConversations(list) {
  return [...list].sort((left, right) => {
    const leftTime = new Date(left.updated_at || left.created_at || 0).getTime()
    const rightTime = new Date(right.updated_at || right.created_at || 0).getTime()
    return rightTime - leftTime
  })
}

function setConversations(list) {
  conversations.value = sortConversations(list)
}

function sortDossiers(list) {
  return [...list].sort((left, right) => {
    const leftTime = new Date(left.updated_at || left.created_at || 0).getTime()
    const rightTime = new Date(right.updated_at || right.created_at || 0).getTime()
    return rightTime - leftTime
  })
}

function setDossiers(list) {
  dossiers.value = sortDossiers(list)
}

function upsertDossierLocal(dossier) {
  if (!dossier) return
  const next = [...dossiers.value]
  const index = next.findIndex((item) => item.id === dossier.id)
  if (index >= 0) next[index] = { ...next[index], ...dossier }
  else next.push(dossier)
  setDossiers(next)
}

function upsertConversationLocal(conversation) {
  if (!conversation) return
  const next = [...conversations.value]
  const index = next.findIndex((item) => item.id === conversation.id)
  if (index >= 0) next[index] = { ...next[index], ...conversation }
  else next.push(conversation)
  setConversations(next)
}

function removeConversationLocal(conversationId) {
  setConversations(conversations.value.filter((item) => item.id !== conversationId))
}

function touchConversationLocal(conversationId, patch = {}) {
  const target = conversations.value.find((item) => item.id === conversationId)
  if (!target) return
  upsertConversationLocal({
    ...target,
    ...patch,
    updated_at: new Date().toISOString(),
  })
}

function resetDossierForm() {
  dossierForm.title = ''
  dossierForm.case_type = ''
  dossierForm.summary = ''
}

function defaultParticipantDrafts(caseType = '') {
  const normalized = String(caseType || '').trim()
  const isLabor = normalized.includes('劳动')
  return {
    title: isLabor ? '劳动争议案卷' : '案情跟踪案卷',
    primary_label: '我',
    primary_role: isLabor ? '劳动者' : '当事人',
    opposing_label: isLabor ? '公司' : '对方',
    opposing_role: isLabor ? '用人单位' : '对方当事人',
  }
}

function applyParticipantAliasesToForm(form, participantAliases = {}, caseType = '') {
  const defaults = defaultParticipantDrafts(caseType)
  const primary = participantAliases?.primary_party || {}
  const opposing = participantAliases?.opposing_party || {}
  form.primary_label = primary.label || defaults.primary_label
  form.primary_role = primary.role || defaults.primary_role
  form.opposing_label = opposing.label || defaults.opposing_label
  form.opposing_role = opposing.role || defaults.opposing_role
}

function resetDossierParticipantForm(caseType = '') {
  applyParticipantAliasesToForm(dossierParticipantForm, {}, caseType)
}

function resetDossierDetailForm() {
  dossierDetailForm.title = ''
  dossierDetailForm.case_type = ''
  dossierDetailForm.summary = ''
  dossierDetailForm.status = 'active'
  resetDossierParticipantForm()
}

function resetTrackedOnboardingForm(caseType = '') {
  const defaults = defaultParticipantDrafts(caseType)
  trackedOnboardingForm.title = defaults.title
  trackedOnboardingForm.primary_label = defaults.primary_label
  trackedOnboardingForm.primary_role = defaults.primary_role
  trackedOnboardingForm.opposing_label = defaults.opposing_label
  trackedOnboardingForm.opposing_role = defaults.opposing_role
}

function clearTrackedOnboardingState() {
  trackedOnboardingState.conversationId = null
  trackedOnboardingState.text = ''
  trackedOnboardingState.forceCurrentCase = false
  trackedOnboardingState.draftText = ''
  trackedOnboardingState.dossierId = null
  resetTrackedOnboardingForm()
}

function applySelectedDossierDetail(dossier) {
  selectedDossierDetail.value = dossier || null
  if (!dossier) {
    resetDossierDetailForm()
    return
  }

  dossierDetailForm.title = dossier.title || ''
  dossierDetailForm.case_type = dossier.case_type || ''
  dossierDetailForm.summary = dossier.summary || ''
  dossierDetailForm.status = dossier.status || 'active'
  applyParticipantAliasesToForm(
    dossierParticipantForm,
    dossier.participant_aliases || {},
    dossier.case_type || ''
  )
}

function resetAnalysis() {
  currentAnalysis.actions = []
  currentAnalysis.sources = []
}

function resetGraph() {
  currentGraph.query = ''
  currentGraph.nodes = []
  currentGraph.edges = []
  currentGraph.tokens = []
  currentGraph.generatedAt = ''
  currentGraph.loading = false
  currentGraph.error = ''
}

function clearCurrentConversationState() {
  currentConversationId.value = null
  currentSessionId.value = ''
  messages.value = []
  resetAnalysis()
  resetGraph()
}

function stopGraphRefreshPolling() {
  if (graphRefreshTimer.value) {
    clearInterval(graphRefreshTimer.value)
    graphRefreshTimer.value = null
  }
  if (graphRefreshStopTimer.value) {
    clearTimeout(graphRefreshStopTimer.value)
    graphRefreshStopTimer.value = null
  }
}

function buildMessageViewModel(message, index = 0) {
  const metadata = message.metadata || {}
  return {
    id: message.id || `${message.role}-${index}`,
    key: `${message.id || 'message'}-${index}`,
    role: message.role === 'assistant' ? 'assistant' : 'user',
    content: message.content || '',
    time: formatTime(message.created_at),
    sources: metadata.sources || [],
    agentActions: metadata.agent_actions || [],
    pending: false,
  }
}

function syncCurrentAnalysisFromMessages() {
  resetAnalysis()
  const latestAssistant = [...messages.value].reverse().find((item) => item.role !== 'user')
  if (!latestAssistant) return
  currentAnalysis.actions = latestAssistant.agentActions || []
  currentAnalysis.sources = latestAssistant.sources || []
}

function applyConversationPayload(payload) {
  const conversation = payload?.conversation
  const backendMessages = Array.isArray(payload?.messages) ? payload.messages : []

  if (conversation) {
    upsertConversationLocal(conversation)
    currentConversationId.value = conversation.id
    currentSessionId.value = conversation.session_id || ''
  }

  messages.value = backendMessages.map((message, index) => buildMessageViewModel(message, index))
  syncCurrentAnalysisFromMessages()
  scrollBottom()
}

async function initializeConversations() {
  try {
    await refreshConversationList()
    if (!conversations.value.length) {
      await createNewConversation({ silent: true })
      return
    }

    await switchConversation(conversations.value[0].id)
  } catch (error) {
    console.error('Failed to initialize conversations:', error)
    ElMessage.error('加载会话列表失败。')
  }
}

async function refreshConversationList(preferredConversationId = null) {
  conversationListLoading.value = true
  try {
    const keyword = conversationSearchKeyword.value.trim()
    const { data } = keyword
      ? await searchConversations(keyword)
      : await getConversations()
    const nextList = Array.isArray(data.conversations)
      ? data.conversations
      : Array.isArray(data.cases)
        ? data.cases
        : []
    setConversations(nextList)

    const activeId = preferredConversationId || currentConversationId.value
    const active = conversations.value.find((item) => item.id === activeId)
    if (active) {
      currentConversationId.value = active.id
      currentSessionId.value = active.session_id || ''
    }
  } finally {
    conversationListLoading.value = false
  }
}

async function refreshDossiers(options = {}) {
  dossierListLoading.value = true
  try {
    const { data } = await getDossiers()
    const nextDossiers = Array.isArray(data.dossiers) ? data.dossiers : []
    setDossiers(nextDossiers)

    if (!nextDossiers.length) {
      selectedDossierId.value = null
      applySelectedDossierDetail(null)
      return
    }

    const preferredId =
      options.preferredDossierId ??
      selectedDossierId.value ??
      currentConversation.value?.dossier_id ??
      nextDossiers[0]?.id

    if (preferredId) {
      selectedDossierId.value = preferredId
    }
  } catch (error) {
    console.error('Failed to fetch dossiers:', error)
    if (!options.silent) {
      ElMessage.error('加载案卷列表失败')
    }
  } finally {
    dossierListLoading.value = false
  }
}

async function loadSelectedDossierDetail(dossierId, options = {}) {
  if (!dossierId) {
    selectedDossierId.value = null
    applySelectedDossierDetail(null)
    return
  }

  selectedDossierId.value = dossierId
  dossierDetailLoading.value = true
  try {
    const { data } = await getDossierDetail(dossierId)
    const dossier = data?.dossier || null
    applySelectedDossierDetail(dossier)
    if (dossier) {
      upsertDossierLocal(dossier)
    }
  } catch (error) {
    console.error('Failed to fetch dossier detail:', error)
    applySelectedDossierDetail(null)
    if (!options.silent) {
      ElMessage.error('Failed to load dossier detail')
    }
  } finally {
    dossierDetailLoading.value = false
  }
}

async function openDossierDrawer() {
  dossierDrawerVisible.value = true
  await refreshDossiers({ preferredDossierId: currentConversation.value?.dossier_id })
  if (selectedDossierId.value) {
    await loadSelectedDossierDetail(selectedDossierId.value, { silent: true })
  }
}

async function handleCreateDossier() {
  const title = String(dossierForm.title || '').trim()
  if (!title) {
    ElMessage.warning('请输入案卷标题')
    return
  }

  dossierSaving.value = true
  try {
    const { data } = await createDossier({
      title,
      case_type: String(dossierForm.case_type || '').trim() || undefined,
      summary: String(dossierForm.summary || '').trim() || undefined,
    })
    if (data?.dossier) {
      upsertDossierLocal(data.dossier)
      await loadSelectedDossierDetail(data.dossier.id, { silent: true })
    } else {
      await refreshDossiers({ silent: true })
      if (selectedDossierId.value) {
        await loadSelectedDossierDetail(selectedDossierId.value, { silent: true })
      }
    }
    resetDossierForm()
    ElMessage.success('案卷已创建，可在下方绑定当前会话')
  } catch (error) {
    console.error('Failed to create dossier:', error)
    ElMessage.error('创建案卷失败')
  } finally {
    dossierSaving.value = false
  }
}

async function handleAttachDossier(dossier) {
  if (!currentConversationId.value || !dossier?.id) return

  dossierBindingId.value = dossier.id
  try {
    const { data } = await attachConversationToDossier(currentConversationId.value, dossier.id)
    if (data?.conversation) {
      upsertConversationLocal(data.conversation)
      if (currentConversationId.value === data.conversation.id) {
        currentSessionId.value = data.conversation.session_id || currentSessionId.value
      }
    }
    await refreshConversationList(currentConversationId.value)
    await refreshDossiers({ silent: true, preferredDossierId: dossier.id })
    await loadSelectedDossierDetail(dossier.id, { silent: true })
    ElMessage.success('当前会话已绑定到案卷')
  } catch (error) {
    console.error('Failed to attach conversation to dossier:', error)
    ElMessage.error('绑定案卷失败')
  } finally {
    dossierBindingId.value = null
  }
}

async function handleSelectDossier(dossier) {
  if (!dossier?.id) return
  await loadSelectedDossierDetail(dossier.id)
}

async function handleUpdateDossierDetail() {
  if (!selectedDossierId.value) return

  const title = String(dossierDetailForm.title || '').trim()
  if (!title) {
    ElMessage.warning('Please enter a dossier title')
    return
  }

  dossierDetailSaving.value = true
  try {
    const { data } = await updateDossier(selectedDossierId.value, {
      title,
      case_type: String(dossierDetailForm.case_type || '').trim() || null,
      summary: String(dossierDetailForm.summary || '').trim() || null,
      status: dossierDetailForm.status || 'active',
    })

    if (data?.dossier) {
      upsertDossierLocal(data.dossier)
    }

    await loadSelectedDossierDetail(selectedDossierId.value, { silent: true })
    ElMessage.success('Dossier updated')
  } catch (error) {
    console.error('Failed to update dossier detail:', error)
    ElMessage.error('Failed to update dossier')
  } finally {
    dossierDetailSaving.value = false
  }
}

async function handleUpdateDossierParticipants() {
  if (!selectedDossierId.value) return

  const primaryLabel = String(dossierParticipantForm.primary_label || '').trim()
  const primaryRole = String(dossierParticipantForm.primary_role || '').trim()
  const opposingLabel = String(dossierParticipantForm.opposing_label || '').trim()
  const opposingRole = String(dossierParticipantForm.opposing_role || '').trim()

  if (!primaryLabel || !primaryRole || !opposingLabel || !opposingRole) {
    ElMessage.warning('请先补全当事人称呼和角色')
    return
  }

  dossierParticipantsSaving.value = true
  try {
    const { data } = await updateDossierParticipants(selectedDossierId.value, {
      participant_aliases: buildParticipantAliasesPayload(dossierParticipantForm),
    })

    if (data?.dossier) {
      upsertDossierLocal(data.dossier)
    }

    await refreshDossiers({ silent: true, preferredDossierId: selectedDossierId.value })
    await loadSelectedDossierDetail(selectedDossierId.value, { silent: true })
    ElMessage.success('当事人称呼已更新')
  } catch (error) {
    console.error('Failed to update dossier participants:', error)
    ElMessage.error('保存当事人称呼失败')
  } finally {
    dossierParticipantsSaving.value = false
  }
}

async function handleGenerateDossierSummary() {
  if (!selectedDossierId.value) return

  dossierSummaryGenerating.value = true
  try {
    await generateDossierSummary(selectedDossierId.value)
    await loadSelectedDossierDetail(selectedDossierId.value, { silent: true })
    await refreshDossiers({ silent: true, preferredDossierId: selectedDossierId.value })
    ElMessage.success('智能摘要已更新')
  } catch (error) {
    console.error('Failed to generate dossier summary:', error)
    const detail = error?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' && detail ? detail : '智能摘要生成失败')
  } finally {
    dossierSummaryGenerating.value = false
  }
}

async function handleCreateConversationFromSelectedDossier() {
  if (!selectedDossierId.value) return
  await createNewConversation({ dossierId: selectedDossierId.value })
}

async function handleConfirmTrackedOnboarding() {
  const conversationId = trackedOnboardingState.conversationId
  if (!conversationId) return

  const title = String(trackedOnboardingForm.title || '').trim()
  const primaryLabel = String(trackedOnboardingForm.primary_label || '').trim()
  const primaryRole = String(trackedOnboardingForm.primary_role || '').trim()
  const opposingLabel = String(trackedOnboardingForm.opposing_label || '').trim()
  const opposingRole = String(trackedOnboardingForm.opposing_role || '').trim()

  if (!title || !primaryLabel || !primaryRole || !opposingLabel || !opposingRole) {
    ElMessage.warning('请先补全案卷名称和当事人称呼')
    return
  }

  trackedOnboardingSubmitting.value = true
  try {
    const conversation =
      conversations.value.find((item) => item.id === conversationId) || currentConversation.value
    let dossierId = trackedOnboardingState.dossierId
    const boundDossier =
      (dossierId && selectedDossierDetail.value?.id === dossierId
        ? selectedDossierDetail.value
        : dossiers.value.find((item) => item.id === dossierId)) || null

    if (!dossierId) {
      const { data } = await createDossier({
        title,
        case_type: String(conversation?.case_type || '').trim() || undefined,
      })
      if (!data?.dossier?.id) {
        throw new Error('Failed to create dossier')
      }
      dossierId = data.dossier.id
      trackedOnboardingState.dossierId = dossierId
      upsertDossierLocal(data.dossier)
    } else if (!boundDossier || title !== (boundDossier.title || '')) {
      const { data } = await updateDossier(dossierId, {
        title,
      })
      if (data?.dossier) {
        upsertDossierLocal(data.dossier)
      }
    }

    const { data: participantsData } = await updateDossierParticipants(dossierId, {
      participant_aliases: buildParticipantAliasesPayload(trackedOnboardingForm),
    })
    if (participantsData?.dossier) {
      upsertDossierLocal(participantsData.dossier)
    }

    const { data: consentData } = await setConversationModeConsent(conversationId, {
      mode: 'TRACKED_CASE',
      consent: 'tracked',
      reason: 'user_selected_tracked',
    })
    if (consentData?.conversation) {
      upsertConversationLocal(consentData.conversation)
    }

    if (conversation?.dossier_id !== dossierId) {
      const { data: attachData } = await attachConversationToDossier(conversationId, dossierId)
      if (attachData?.conversation) {
        upsertConversationLocal(attachData.conversation)
      }
    }

    await refreshConversationList(conversationId)
    await refreshDossiers({ silent: true, preferredDossierId: dossierId })
    await loadSelectedDossierDetail(dossierId, { silent: true })
    finalizeTrackedOnboarding({ cancelled: false })
    clearTrackedOnboardingState()
  } catch (error) {
    console.error('Failed to complete tracked onboarding:', error)
    const detail = error?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' && detail ? detail : '案卷初始化失败')
  } finally {
    trackedOnboardingSubmitting.value = false
  }
}

async function createNewConversation(options = {}) {
  try {
    const payload = {}
    if (options.dossierId) {
      payload.dossier_id = options.dossierId
    }

    const { data } = await createConversation(payload)
    const conversation = data.conversation
    conversationSearchKeyword.value = ''
    sessionBatchMode.value = false
    upsertConversationLocal(conversation)
    await switchConversation(conversation.id)
    if (options.dossierId) {
      await refreshDossiers({ silent: true, preferredDossierId: options.dossierId })
      await loadSelectedDossierDetail(options.dossierId, { silent: true })
    }
    if (!options.silent) {
      ElMessage.success('已创建新会话。')
    }
  } catch (error) {
    console.error('Failed to create conversation:', error)
    ElMessage.error('创建会话失败。')
  }
}

async function switchConversation(conversationId) {
  if (!conversationId) return
  stopGraphRefreshPolling()
  try {
    stopGraphRefreshPolling()
    resetAnalysis()
    resetGraph()
    const { data } = await getConversationMessages(conversationId)
    applyConversationPayload(data)
    await fetchConversationGraph(conversationId)
  } catch (error) {
    console.error('Failed to load conversation messages:', error)
    ElMessage.error('加载会话消息失败。')
  }
}

async function promptRenameConversation(session) {
  if (!session?.id) return

  try {
    const { value } = await ElMessageBox.prompt('请输入新的会话标题', '重命名会话', {
      confirmButtonText: '确认',
      cancelButtonText: '取消',
      inputValue: session.title || '',
      inputPattern: /\S+/,
      inputErrorMessage: '标题不能为空',
    })

    const title = value.trim()
    const { data } = await updateConversationTitle(session.id, title)
    upsertConversationLocal(data.conversation)
    if (currentConversationId.value === session.id) {
      currentSessionId.value = data.conversation.session_id || currentSessionId.value
    }
    ElMessage.success('会话标题已更新。')
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    console.error('Failed to rename conversation:', error)
    ElMessage.error('更新会话标题失败。')
  }
}

async function handleConversationSearch(keyword) {
  conversationSearchKeyword.value = keyword || ''
  await refreshConversationList(currentConversationId.value)
}

function handleSessionBatchModeChange(enabled) {
  sessionBatchMode.value = Boolean(enabled)
}

async function handleBatchDelete(conversationIds) {
  return handleBatchDeleteV2(conversationIds)
  if (!conversationIds?.length) return

  try {
    await ElMessageBox.confirm(
      `确定删除选中的 ${conversationIds.length} 个会话吗？该操作会同时尝试清理对应案情图谱。`,
      '批量删除确认',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      }
    )
  } catch (error) {
    return
  }

  try {
    const { data } = await batchDeleteConversations(conversationIds)
    const deletedIds = new Set(data.deleted || [])
    const currentDeleted = currentConversationId.value != null && deletedIds.has(currentConversationId.value)

    sessionBatchMode.value = false
    await refreshConversationList(currentDeleted ? null : currentConversationId.value)

    if (currentDeleted) {
      const nextConversation = conversations.value[0]
      if (nextConversation) {
        await switchConversation(nextConversation.id)
      } else {
        clearCurrentConversationState()
        await createNewConversation({ silent: true })
      }
    }

    if (Array.isArray(data.graph_warnings) && data.graph_warnings.length) {
      console.warn('Batch graph cleanup warnings:', data.graph_warnings)
      ElMessage.warning('部分图谱清理失败，请稍后重试或查看日志')
    }

    if (Array.isArray(data.skipped) && data.skipped.length) {
      ElMessage.warning(`已跳过 ${data.skipped.length} 个无效会话`)
    } else if ((data.deleted || []).length) {
      ElMessage.success(`已删除 ${data.deleted.length} 个会话`)
    }
  } catch (error) {
    console.error('Failed to batch delete conversations:', error)
    ElMessage.error('批量删除会话失败')
  }
}

async function removeConversation(conversationId) {
  return removeConversationV2(conversationId)
  if (!conversationId) return

  ElMessageBox.confirm('确定删除该会话及其全部消息吗？', '删除确认', { type: 'warning' })
    .then(async () => {
      try {
        const { data } = await deleteConversation(conversationId)
        removeConversationLocal(conversationId)

        if (conversationId === currentConversationId.value) {
          const nextConversation = conversations.value[0]
          if (nextConversation) {
            await switchConversation(nextConversation.id)
          } else {
            clearCurrentConversationState()
            await createNewConversation({ silent: true })
          }
        }

        ElMessage.success('会话已删除。')
      } catch (error) {
        console.error('Failed to delete conversation:', error)
        ElMessage.error('删除会话失败。')
      }
    })
    .catch(() => {})
}

async function fetchConversationGraph(conversationId, query = '', options = {}) {
  if (!conversationId) {
    resetGraph()
    return
  }

  const requestId = ++latestGraphRequestId.value
  const preserveData = Boolean(options.preserveData)
  currentGraph.query = query || currentConversation.value?.title || `会话 ${conversationId} 案情图谱`
  if (!preserveData) {
    currentGraph.nodes = []
    currentGraph.edges = []
    currentGraph.tokens = []
    currentGraph.generatedAt = ''
  }
  currentGraph.loading = !options.silent
  currentGraph.error = ''

  try {
    const { data } = await getConversationGraph(conversationId)
    if (requestId !== latestGraphRequestId.value) return
    currentGraph.query = data.query || query || currentGraph.query
    currentGraph.nodes = data.nodes || []
    currentGraph.edges = data.edges || []
    currentGraph.tokens = data.tokens || []
    currentGraph.generatedAt = data.generated_at || ''
  } catch (error) {
    if (requestId !== latestGraphRequestId.value) return
    currentGraph.error = error?.response?.data?.detail || '图谱加载失败。'
  } finally {
    if (requestId === latestGraphRequestId.value && !options.silent) {
      currentGraph.loading = false
    }
  }
}

async function ensureConversationTitle(userText) {
  if (!currentConversation.value) return
  if ((currentConversation.value.title || '').trim()) return

  const title = userText.slice(0, 15).trim()
  if (!title) return

  touchConversationLocal(currentConversation.value.id, { title })
  try {
    const { data } = await updateConversationTitle(currentConversation.value.id, title)
    upsertConversationLocal(data.conversation)
  } catch (error) {
    console.error('Failed to set first message title:', error)
  }
}

function startGraphRefreshPolling(conversationId, previousNodeCount, query = '') {
  stopGraphRefreshPolling()
  if (!conversationId) return

  graphRefreshTimer.value = setInterval(async () => {
    try {
      await fetchConversationGraph(conversationId, query, { preserveData: true, silent: true })
      if ((currentGraph.nodes?.length || 0) !== previousNodeCount) {
        stopGraphRefreshPolling()
      }
    } catch (error) {
      console.error('Graph refresh polling failed:', error)
      stopGraphRefreshPolling()
    }
  }, 3000)

  graphRefreshStopTimer.value = setTimeout(() => {
    stopGraphRefreshPolling()
  }, 15000)
}

function updatePendingMessage(tempId, patch = {}) {
  const target = messages.value.find((item) => item.id === tempId)
  if (!target) return
  Object.assign(target, patch)
}

function removeMessageById(messageId) {
  messages.value = messages.value.filter((item) => item.id !== messageId)
}

async function createCaseForResend() {
  const { data } = await createConversation({})
  const conversation = data.conversation
  upsertConversationLocal(conversation)
  await switchConversation(conversation.id)
  ElMessage.success('已为新法律问题建立独立卷宗。')
  return conversation
}

async function resolveCaseBoundarySuggestion(message) {
  try {
    await ElMessageBox.confirm(
      message || '发现您似乎开启了一个新的法律问题，是否新建独立卷宗？',
      '检测到新案件',
      {
        confirmButtonText: '是，新建卷宗',
        cancelButtonText: '否，继续在当前补充',
        distinguishCancelAndClose: true,
        type: 'warning',
      }
    )
    return 'new_case'
  } catch (error) {
    if (error === 'cancel') {
      return 'stay_current'
    }
    if (error === 'close') {
      return 'cancelled'
    }
    throw error
  }
}

function conversationModeLabel(mode) {
  switch (String(mode || '').toUpperCase()) {
    case 'LEGAL_QA':
      return '法律问答'
    case 'CASE_ANALYSIS':
      return '案情分析'
    case 'TRACKED_CASE':
      return '持续跟踪'
    default:
      return '未分类'
  }
}

function conversationModeTagType(mode) {
  switch (String(mode || '').toUpperCase()) {
    case 'LEGAL_QA':
      return 'success'
    case 'CASE_ANALYSIS':
      return 'warning'
    case 'TRACKED_CASE':
      return 'danger'
    default:
      return 'info'
  }
}

function dossierStatusLabel(status) {
  switch (String(status || '').toLowerCase()) {
    case 'archived':
      return '已归档'
    case 'closed':
      return '已关闭'
    default:
      return '进行中'
  }
}

function dossierStatusTagType(status) {
  switch (String(status || '').toLowerCase()) {
    case 'archived':
      return 'info'
    case 'closed':
      return 'danger'
    default:
      return 'success'
  }
}

function persistenceConsentLabel(consent) {
  switch (String(consent || '').toLowerCase()) {
    case 'tracked':
      return '持续跟踪'
    case 'temporary':
      return '仅本次分析'
    default:
      return '未确认'
  }
}

function formatDossierConversationTime(value) {
  if (!value) return ''
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function buildParticipantAliasesPayload(form) {
  return {
    primary_party: {
      label: String(form.primary_label || '').trim(),
      role: String(form.primary_role || '').trim(),
      party_side: 'claimant',
    },
    opposing_party: {
      label: String(form.opposing_label || '').trim(),
      role: String(form.opposing_role || '').trim(),
      party_side: 'opponent',
    },
  }
}

function resolveTrackedOnboardingDossierSeed(conversationId) {
  const conversation =
    conversations.value.find((item) => item.id === conversationId) || currentConversation.value
  const boundDossierId = conversation?.dossier_id || null
  const boundDossier =
    (boundDossierId && selectedDossierDetail.value?.id === boundDossierId
      ? selectedDossierDetail.value
      : dossiers.value.find((item) => item.id === boundDossierId)) || null

  const defaults = defaultParticipantDrafts(conversation?.case_type || '')
  trackedOnboardingState.dossierId = boundDossierId
  trackedOnboardingForm.title = boundDossier?.title || defaults.title
  applyParticipantAliasesToForm(
    trackedOnboardingForm,
    boundDossier?.participant_aliases || {},
    conversation?.case_type || ''
  )
}

function finalizeTrackedOnboarding(result) {
  trackedOnboardingVisible.value = false
  const resolver = trackedOnboardingResolver
  trackedOnboardingResolver = null
  if (resolver) {
    resolver(result)
  }
}

async function resolveTrackedOnboarding(options = {}) {
  trackedOnboardingState.conversationId = options.conversationId || currentConversationId.value
  trackedOnboardingState.text = options.text || ''
  trackedOnboardingState.forceCurrentCase = Boolean(options.forceCurrentCase)
  trackedOnboardingState.draftText = options.draftText || ''
  resolveTrackedOnboardingDossierSeed(trackedOnboardingState.conversationId)
  trackedOnboardingVisible.value = true
  return new Promise((resolve) => {
    trackedOnboardingResolver = resolve
  })
}

function handleCancelTrackedOnboarding() {
  if (trackedOnboardingSubmitting.value) return
  finalizeTrackedOnboarding({ cancelled: true })
  clearTrackedOnboardingState()
}

async function resolveModeConsentSuggestion(prompt) {
  const message = [
    prompt || '我注意到你正在描述一个具体案情。是否要保存为持续跟踪的案情会话？',
    '',
    '当前聊天记录仍会保存在会话历史中；选择持续跟踪后，系统会把案情整理为可持续维护的结构化案情。',
  ].join('\n')

  try {
    await ElMessageBox.confirm(
      message,
      '模式确认',
      {
        confirmButtonText: '保存并持续跟踪',
        cancelButtonText: '仅本次分析',
        distinguishCancelAndClose: true,
        type: 'warning',
      }
    )
    return 'tracked'
  } catch (error) {
    if (error === 'cancel') {
      return 'temporary'
    }
    if (error === 'close') {
      return 'cancelled'
    }
    throw error
  }
}

function restoreDraftInput(draftText, feedbackMessage = '', feedbackType = 'info') {
  inputText.value = draftText
  if (!feedbackMessage) return

  if (feedbackType === 'error') {
    ElMessage.error(feedbackMessage)
    return
  }

  ElMessage.info(feedbackMessage)
}

async function sendImpl(textOverride = null, options = {}) {
  if (!systemStatus.ready) {
    ElMessage.warning('系统仍在初始化，请稍后。')
    return
  }

  const baseConversationId = options.conversationId || currentConversationId.value
  if (!baseConversationId) {
    ElMessage.warning('请先新建或选择一份卷宗。')
    return
  }

  const finalStr = (typeof textOverride === 'string' ? textOverride : inputText.value) || ''
  const text = finalStr.trim()
  if (!text || sending.value) return
  const draftText = text

  const forceCurrentCase = Boolean(options.forceCurrentCase)
  const modeConsentEnabled = options.modeConsentEnabled !== false
  const restoreDraftOnErrorText =
    typeof options.restoreDraftOnErrorText === 'string' ? options.restoreDraftOnErrorText : null
  const tempKey = `temp-user-${Date.now()}`
  const nowIso = new Date().toISOString()
  const previousNodeCount = currentGraph.nodes?.length || 0

  messages.value.push({
    id: tempKey,
    key: tempKey,
    role: 'user',
    content: text,
    time: formatTime(nowIso),
    sources: [],
    agentActions: [],
    pending: true,
  })

  inputText.value = ''
  sending.value = true
  graphSidebarCollapsed.value = false
  touchConversationLocal(baseConversationId)
  currentGraph.loading = true
  currentGraph.error = ''
  currentGraph.query = text
  scrollBottom()

  try {
    const initRes = await sendMessage(text, baseConversationId, {
      forceCurrentCase,
      isPreciseMode: proactiveGraphMode.value,
      proactiveGraphMode: proactiveGraphMode.value,
      modeConsentEnabled,
    })
    const initData = initRes?.data || {}

    if (initData.action === 'suggest_new_case') {
      removeMessageById(tempKey)
      const nextAction = await resolveCaseBoundarySuggestion(initData.message)
      sending.value = false
      currentGraph.loading = false

      if (nextAction === 'new_case') {
        try {
          const newConversation = await createCaseForResend()
          if (!newConversation?.id) {
            restoreDraftInput(draftText, '新案情确认失败，输入内容已恢复', 'error')
            return
          }

          await sendImpl(text, {
            conversationId: newConversation.id,
            forceCurrentCase: false,
            modeConsentEnabled: false,
            restoreDraftOnErrorText: draftText,
          })
        } catch (error) {
          console.error('Failed to continue suggest_new_case flow:', error)
          restoreDraftInput(draftText, '新案情确认失败，输入内容已恢复', 'error')
        }
        return
      }

      if (nextAction === 'cancelled') {
        restoreDraftInput(draftText, '已取消，输入内容已恢复')
        return
      }

      await sendImpl(text, {
        conversationId: baseConversationId,
        forceCurrentCase: true,
        modeConsentEnabled: false,
        restoreDraftOnErrorText: draftText,
      })
      return
    }

    if (initData.action === 'mode_consent') {
      removeMessageById(tempKey)
      const nextAction = await resolveModeConsentSuggestion(initData.consent_prompt)
      sending.value = false
      currentGraph.loading = false

      if (nextAction === 'cancelled') {
        restoreDraftInput(draftText, '已取消，输入内容已恢复')
        return
      }

      if (nextAction === 'tracked') {
        const onboardingResult = await resolveTrackedOnboarding({
          conversationId: baseConversationId,
          text,
          forceCurrentCase,
          draftText,
        })
        if (onboardingResult?.cancelled) {
          restoreDraftInput(draftText, '已取消，输入内容已恢复')
          return
        }

        await sendImpl(text, {
          conversationId: baseConversationId,
          forceCurrentCase,
          modeConsentEnabled: false,
          restoreDraftOnErrorText: draftText,
        })
        return
      }

      const payload =
        nextAction === 'tracked'
          ? {
              mode: 'TRACKED_CASE',
              consent: 'tracked',
              reason: 'user_selected_tracked',
            }
          : {
              mode: 'CASE_ANALYSIS',
              consent: 'temporary',
              reason: 'user_selected_temporary',
            }

      try {
        const { data: consentData } = await setConversationModeConsent(baseConversationId, payload)
        if (consentData?.conversation) {
          upsertConversationLocal(consentData.conversation)
        }
      } catch (error) {
        console.error('Failed to persist mode consent:', error)
        restoreDraftInput(draftText, '模式确认失败，输入内容已恢复', 'error')
        return
      }

      await sendImpl(text, {
        conversationId: baseConversationId,
        forceCurrentCase,
        modeConsentEnabled: false,
        restoreDraftOnErrorText: draftText,
      })
      return
    }

    const data = initData.task_id ? (await initRes).data : initData
    updatePendingMessage(tempKey, { pending: false })

    messages.value.push({
      id: `assistant-${Date.now()}`,
      key: `assistant-${Date.now()}`,
      role: 'assistant',
      content: data.response || '未返回有效内容。',
      time: formatTime(new Date().toISOString()),
      sources: data.sources || [],
      agentActions: data.agent_actions || [],
      pending: false,
    })

    currentAnalysis.actions = data.agent_actions || []
    currentAnalysis.sources = data.sources || []
    void ensureConversationTitle(text)
    await refreshConversationList(baseConversationId)
    await fetchConversationGraph(baseConversationId, text)
    startGraphRefreshPolling(baseConversationId, previousNodeCount, text)
  } catch (error) {
    console.error('Failed to send message:', error)
    if (restoreDraftOnErrorText) {
      restoreDraftInput(restoreDraftOnErrorText, '发送失败，输入内容已恢复', 'error')
    }
    updatePendingMessage(tempKey, { pending: false })
    messages.value.push({
      id: `assistant-error-${Date.now()}`,
      key: `assistant-error-${Date.now()}`,
      role: 'assistant',
      content: `请求失败：${error.message || '未知错误'}`,
      time: formatTime(new Date().toISOString()),
      sources: [],
      agentActions: [],
      pending: false,
    })
  } finally {
    sending.value = false
    currentGraph.loading = false
    scrollBottom()
  }
}

function handleKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    void sendImpl()
  }
}

async function handleSummary() {
  if (!currentConversationId.value) return
  stopGraphRefreshPolling()

  summaryLoading.value = true
  try {
    const { data } = await getConversationSummary(currentConversationId.value)
    messages.value.push({
      id: `summary-${Date.now()}`,
      key: `summary-${Date.now()}`,
      role: 'assistant',
      content: `会话摘要：\n${data.summary}`,
      time: formatTime(new Date().toISOString()),
      sources: [],
      agentActions: [],
      pending: false,
    })
    scrollBottom()
    ElMessage.success('摘要生成完成。')
  } catch (error) {
    console.error('Failed to summarize conversation:', error)
    ElMessage.error('生成摘要失败。')
  } finally {
    summaryLoading.value = false
  }
}

function handleExport() {
  if (!currentConversationId.value) return

  const payload = {
    conversation_id: currentConversationId.value,
    session_id: currentSessionId.value,
    messages: messages.value,
  }
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = `会话记录_${currentConversationId.value}.json`
  link.click()
  URL.revokeObjectURL(link.href)
  ElMessage.success('会话已导出。')
}

function handleLogout() {
  clearAuthSession()
  ElMessage.success('已退出登录。')
  router.replace('/login')
}

function handleClear() {
  return handleClearReset()
  if (!currentConversationId.value) return

  ElMessageBox.confirm('确定清空当前会话的全部消息吗？', '清空确认', { type: 'warning' })
    .then(async () => {
      try {
        await clearConversationMessages(currentConversationId.value)
        messages.value = []
        resetAnalysis()
        resetGraph()
        ElMessage.success('当前会话已清空。')
      } catch (error) {
        console.error('Failed to clear conversation:', error)
        ElMessage.error('清空会话失败。')
      }
    })
    .catch(() => {})
}

function handleClearLegacy() {
  return handleClearReset()
  if (!currentConversationId.value) return

  ElMessageBox.confirm(
    '这会删除当前会话的聊天记录，并重置案情摘要、结构化案情状态和案情图谱。确定继续吗？',
    '清空聊天并重置案情',
    {
      confirmButtonText: '继续',
      cancelButtonText: '取消',
      type: 'warning',
    }
  )
    .then(async () => {
      try {
        const { data } = await clearConversationMessages(currentConversationId.value)
        messages.value = []
        resetAnalysis()
        resetGraph()
        touchConversationLocal(currentConversationId.value, {
          case_summary: '',
          case_type: '',
          case_state: null,
          conversation_mode: 'UNKNOWN',
          persistence_consent: 'unknown',
          mode_confirmed_at: null,
          mode_reason: null,
        })
        if (data?.warning) {
          ElMessage.warning('聊天已清空，但图谱清理可能未完成。')
        } else {
          ElMessage.success('聊天已清空，案情状态已重置。')
        }
      } catch (error) {
        console.error('Failed to clear conversation:', error)
        ElMessage.error('清空当前会话失败')
      }
    })
    .catch(() => {})
}

async function handleBatchDeleteV2(conversationIds) {
  if (!conversationIds?.length) return

  try {
    await ElMessageBox.confirm(
      `确定删除选中的 ${conversationIds.length} 个会话吗？该操作会同时尝试清理对应案情图谱。`,
      '批量删除确认',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      }
    )
  } catch (error) {
    return
  }

  try {
    const { data } = await batchDeleteConversations(conversationIds)
    const deletedIds = new Set(data.deleted || [])
    const currentDeleted = currentConversationId.value != null && deletedIds.has(currentConversationId.value)

    sessionBatchMode.value = false
    await refreshConversationList(currentDeleted ? null : currentConversationId.value)

    if (currentDeleted) {
      const nextConversation = conversations.value[0]
      if (nextConversation) {
        await switchConversation(nextConversation.id)
      } else {
        clearCurrentConversationState()
      }
    }

    if (Array.isArray(data.graph_warnings) && data.graph_warnings.length) {
      console.warn('Batch graph cleanup warnings:', data.graph_warnings)
      ElMessage.warning('部分图谱清理失败，请稍后重试或查看日志')
    }

    if (Array.isArray(data.skipped) && data.skipped.length) {
      ElMessage.warning(`已跳过 ${data.skipped.length} 个无效会话`)
    } else if ((data.deleted || []).length) {
      ElMessage.success(`已删除 ${data.deleted.length} 个会话`)
    }
  } catch (error) {
    console.error('Failed to batch delete conversations:', error)
    ElMessage.error('批量删除会话失败')
  }
}

async function removeConversationV2(conversationId) {
  if (!conversationId) return

  ElMessageBox.confirm('确定删除该会话及其全部消息吗？', '删除确认', { type: 'warning' })
    .then(async () => {
      try {
        const { data } = await deleteConversation(conversationId)
        removeConversationLocal(conversationId)

        if (conversationId === currentConversationId.value) {
          const nextConversation = conversations.value[0]
          if (nextConversation) {
            await switchConversation(nextConversation.id)
          } else {
            clearCurrentConversationState()
          }
        }

        if (data?.warning) {
          ElMessage.warning('会话已删除，但图谱清理可能未完成。')
        } else {
          ElMessage.success('会话已删除。')
        }
      } catch (error) {
        console.error('Failed to delete conversation:', error)
        ElMessage.error('删除会话失败。')
      }
    })
    .catch(() => {})
}

function handleClearReset() {
  if (!currentConversationId.value) return

  ElMessageBox.confirm(
    '这会删除当前会话的聊天记录，并重置案情摘要、结构化案情状态和案情图谱。确定继续吗？',
    '清空聊天并重置案情',
    {
      confirmButtonText: '继续',
      cancelButtonText: '取消',
      type: 'warning',
    }
  )
    .then(async () => {
      try {
        const { data } = await clearConversationMessages(currentConversationId.value)
        messages.value = []
        resetAnalysis()
        resetGraph()
        touchConversationLocal(currentConversationId.value, {
          case_summary: '',
          case_type: '',
          case_state: null,
          conversation_mode: 'UNKNOWN',
          persistence_consent: 'unknown',
          mode_confirmed_at: null,
          mode_reason: null,
        })
        if (data?.warning) {
          ElMessage.warning('聊天已清空，但图谱清理可能未完成。')
        } else {
          ElMessage.success('聊天已清空，案情状态已重置。')
        }
      } catch (error) {
        console.error('Failed to clear conversation:', error)
        ElMessage.error('清空当前会话失败')
      }
    })
    .catch(() => {})
}

async function handleFeedback({ rating, comment }) {
  try {
    await submitFeedback({
      session_id: currentSessionId.value,
      turn_index: messages.value.filter((message) => message.role === 'user').length,
      rating,
      comment,
      feedback_type: 'general',
    })
    ElMessage.success('反馈已提交。')
  } catch (error) {
    console.error('Failed to submit feedback:', error)
    ElMessage.error('提交反馈失败。')
  }
}

async function refreshSystemStatus() {
  try {
    const { data } = await getSystemStatus()
    systemStatus.status = data.status || 'loading'
    systemStatus.ready = Boolean(data.ready)
    systemStatus.healthy = Boolean(data.ready)
    systemStatus.components = {
      chroma: data.components?.chroma || 'loading',
      neo4j: data.components?.neo4j || 'loading',
      llm: data.components?.llm || 'loading',
      celery: data.components?.celery || 'loading',
    }
    systemStatus.ragReady = systemStatus.components.chroma === 'connected'
    systemStatus.docCount = data.knowledge?.total_doc_count || 0
    systemStatus.model = data.model || ''
    systemStatus.tools = data.tools || []

    if (systemStatus.ready) {
      stopSystemStatusPolling()
    }
  } catch (error) {
    console.error('Failed to fetch system status:', error)
    systemStatus.status = 'loading'
    systemStatus.ready = false
    systemStatus.healthy = false
  }
}

function startSystemStatusPolling() {
  void refreshSystemStatus()
  if (statusTimer.value) clearInterval(statusTimer.value)
  statusTimer.value = setInterval(refreshSystemStatus, 1000)
}

function stopSystemStatusPolling() {
  if (statusTimer.value) {
    clearInterval(statusTimer.value)
    statusTimer.value = null
  }
}

function formatTime(value) {
  if (!value) return ''
  return new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function scrollBottom() {
  nextTick(() => {
    if (scrollRef.value) {
      scrollRef.value.setScrollTop(messageListRef.value?.scrollHeight || 99999)
    }
  })
}
</script>

<style>
html,
body,
#app {
  margin: 0;
  padding: 0;
  height: 100%;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
}
</style>

<style scoped>
.app-container {
  height: 100vh;
  background: #f5f7fa;
  padding-top: 44px;
}

.system-banner {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 30;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 44px;
  color: #fff;
}

.system-banner.loading {
  background: linear-gradient(90deg, #d97706, #f59e0b);
}

.system-banner.ready {
  background: linear-gradient(90deg, #15803d, #22c55e);
}

.system-banner-content {
  width: min(1320px, calc(100vw - 24px));
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  font-size: 13px;
}

.system-banner-main {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}

.system-banner-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  opacity: 0.92;
}

.banner-icon {
  font-size: 16px;
}

.rotating {
  animation: spin 1s linear infinite;
}

.app-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  padding: 0 20px;
  height: 56px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #303133;
}

.app-title {
  font-size: 16px;
  font-weight: 600;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 4px;
}

.user-chip {
  display: inline-flex;
  align-items: center;
  min-height: 30px;
  padding: 0 12px;
  border-radius: 999px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 12px;
  font-weight: 700;
}

.clear-reset-button {
  font-size: 14px;
}

.clear-reset-button :deep(.el-icon) {
  font-size: 14px;
  margin-right: 4px;
}


.main-area {
  height: calc(100vh - 100px);
}

.side-panel {
  background: #fff;
  padding: 16px;
  overflow: hidden;
}

.left-panel {
  border-right: 1px solid #e4e7ed;
}

.right-panel {
  border-left: 1px solid #e4e7ed;
  transition: width 0.25s ease;
}

.right-panel.collapsed {
  padding-left: 10px;
  padding-right: 10px;
}

.chat-area {
  position: relative;
  display: flex;
  flex-direction: column;
  padding: 0;
  overflow: hidden;
}

.local-model-card {
  margin: 16px 24px 0;
  padding: 14px 16px;
  border: 1px solid #dbeafe;
  border-radius: 14px;
  background: linear-gradient(135deg, #eff6ff, #f8fafc);
}

.local-model-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.local-model-title {
  font-size: 14px;
  font-weight: 700;
  color: #1f2937;
}

.local-model-status {
  margin-top: 8px;
  font-size: 12px;
  font-weight: 600;
}

.local-model-status.is-off {
  color: #6b7280;
}

.local-model-status.is-on {
  color: #15803d;
}

.local-model-status.is-error {
  color: #b91c1c;
}

.conversation-mode-card {
  margin: 12px 24px 0;
  padding: 12px 16px;
  border: 1px solid #e5e7eb;
  border-radius: 14px;
  background: #fff;
}

.conversation-mode-main {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.conversation-mode-copy {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.conversation-mode-title {
  font-size: 13px;
  font-weight: 700;
  color: #111827;
}

.conversation-mode-subtitle {
  font-size: 12px;
  color: #6b7280;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conversation-mode-tags {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
}

.conversation-dossier-row {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #eef2f7;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.conversation-dossier-copy {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.conversation-dossier-title {
  font-size: 12px;
  font-weight: 700;
  color: #111827;
}

.conversation-dossier-subtitle {
  font-size: 12px;
  color: #6b7280;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dossier-drawer {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.dossier-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.dossier-toolbar-copy {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.dossier-toolbar-title,
.dossier-section-title,
.dossier-current-title {
  font-size: 14px;
  font-weight: 700;
  color: #111827;
}

.dossier-toolbar-subtitle,
.dossier-current-hint {
  font-size: 12px;
  color: #6b7280;
  line-height: 1.5;
}

.dossier-current-card,
.dossier-create-card,
.dossier-list-card {
  padding: 14px;
  border: 1px solid #e5e7eb;
  border-radius: 14px;
  background: #fff;
}

.dossier-current-name {
  margin-top: 6px;
  font-size: 13px;
  font-weight: 600;
  color: #111827;
}

.dossier-create-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dossier-list-status {
  font-size: 12px;
  color: #6b7280;
}

.dossier-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dossier-item {
  padding: 12px;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  background: #f8fafc;
}

.dossier-item.active {
  border-color: #93c5fd;
  background: #eff6ff;
}

.dossier-item-main {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.dossier-item-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.dossier-item-title {
  font-size: 13px;
  font-weight: 700;
  color: #111827;
}

.dossier-item-meta {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #4b5563;
}

.dossier-item-summary {
  font-size: 12px;
  line-height: 1.5;
  color: #6b7280;
}

.dossier-detail-card {
  padding: 14px;
  border: 1px solid #e5e7eb;
  border-radius: 14px;
  background: #fff;
}

.dossier-detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.dossier-detail-body {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.dossier-detail-meta {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #4b5563;
}

.dossier-auto-summary-section {
  padding: 12px;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  background: #f8fafc;
}

.dossier-participants-section {
  padding: 12px;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  background: #f8fafc;
}

.dossier-auto-summary-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.dossier-participants-hint {
  margin-top: 8px;
  font-size: 12px;
  line-height: 1.6;
  color: #6b7280;
}

.dossier-participants-grid,
.tracked-onboarding-grid {
  margin-top: 10px;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.dossier-auto-summary-meta {
  margin-top: 8px;
  display: inline-flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 12px;
  color: #6b7280;
}

.dossier-auto-summary-content {
  margin-top: 10px;
  white-space: pre-wrap;
  line-height: 1.7;
  font-size: 13px;
  color: #111827;
}

.tracked-onboarding-copy {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tracked-onboarding-title {
  font-size: 15px;
  font-weight: 700;
  color: #111827;
}

.tracked-onboarding-subtitle,
.tracked-onboarding-hint {
  font-size: 12px;
  line-height: 1.6;
  color: #6b7280;
}

.tracked-onboarding-form {
  margin-top: 14px;
}

.tracked-onboarding-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.dossier-detail-conversations {
  padding-top: 12px;
  border-top: 1px solid #eef2f7;
}

.dossier-detail-conversations-title {
  margin-bottom: 10px;
  font-size: 13px;
  font-weight: 700;
  color: #111827;
}

.dossier-conversation-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dossier-conversation-item {
  padding: 10px 12px;
  border-radius: 12px;
  background: #f8fafc;
  border: 1px solid #e5e7eb;
}

.dossier-conversation-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.dossier-conversation-title {
  font-size: 13px;
  font-weight: 600;
  color: #111827;
}

.dossier-conversation-time {
  font-size: 11px;
  color: #6b7280;
  flex: 0 0 auto;
}

.dossier-conversation-tags {
  margin-top: 8px;
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.chat-scroll {
  flex: 1;
  padding: 20px 24px;
}

.message-list {
  max-width: 800px;
  margin: 0 auto;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 260px;
  text-align: center;
  color: #6b7280;
}

.empty-title {
  font-size: 18px;
  font-weight: 700;
  color: #1f2937;
  line-height: 1.4;
}

.empty-title {
  font-size: 0;
}

.empty-title::after {
  content: '新建卷宗';
  font-size: 18px;
  font-weight: 700;
  color: #1f2937;
}

.empty-subtitle {
  margin-top: 8px;
  font-size: 13px;
  line-height: 1.6;
  max-width: 420px;
}

.empty-subtitle {
  font-size: 0;
}

.empty-subtitle::after {
  content: '从左侧选择我的案件卷宗，或先新建一份独立卷宗后开始法律咨询。';
  font-size: 13px;
  line-height: 1.6;
  color: #6b7280;
}

.input-area {
  display: flex;
  gap: 12px;
  padding: 12px 24px 16px;
  background: #fff;
  border-top: 1px solid #e4e7ed;
  align-items: flex-end;
}

.input-area.locked {
  opacity: 0.86;
}

.input-area .el-textarea {
  flex: 1;
}

.input-stack {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.mode-switch-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.mode-switch-label {
  font-size: 13px;
  font-weight: 600;
  color: #374151;
}

.input-area .el-button {
  height: 54px;
  min-width: 80px;
}

@media (max-width: 768px) {
  .dossier-participants-grid,
  .tracked-onboarding-grid {
    grid-template-columns: 1fr;
  }
}

.init-overlay {
  position: absolute;
  inset: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(245, 247, 250, 0.78);
  backdrop-filter: blur(4px);
}

.init-card {
  width: min(420px, calc(100vw - 64px));
  padding: 28px 24px;
  border-radius: 18px;
  background: #fff;
  box-shadow: 0 18px 50px rgba(15, 23, 42, 0.14);
  text-align: center;
}

.init-icon {
  font-size: 28px;
  color: #d97706;
}

.init-title {
  margin-top: 12px;
  font-size: 20px;
  font-weight: 700;
  color: #1f2937;
}

.init-subtitle {
  margin-top: 8px;
  font-size: 13px;
  line-height: 1.6;
  color: #6b7280;
}

.loading-msg {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
}

.loading-msg .bubble-wrap {
  flex: 1;
}

.loading-bubble {
  display: inline-flex;
  gap: 4px;
  padding: 14px 20px;
  background: #f4f4f5;
  border-radius: 12px;
  border-top-left-radius: 4px;
}

.dot {
  width: 8px;
  height: 8px;
  background: #c0c4cc;
  border-radius: 50%;
  animation: bounce 1.4s infinite ease-in-out both;
}

.dot:nth-child(1) {
  animation-delay: 0s;
}

.dot:nth-child(2) {
  animation-delay: 0.2s;
}

.dot:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes bounce {
  0%,
  80%,
  100% {
    transform: scale(0.6);
    opacity: 0.4;
  }

  40% {
    transform: scale(1);
    opacity: 1;
  }
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

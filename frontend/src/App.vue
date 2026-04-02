<template>
  <div class="system-banner" :class="systemStatus.ready ? 'ready' : 'loading'">
    <div class="system-banner-content">
      <div class="system-banner-main">
        <el-icon v-if="systemStatus.ready" class="banner-icon"><CircleCheckFilled /></el-icon>
        <el-icon v-else class="banner-icon rotating"><Loading /></el-icon>
        <span>{{ systemStatus.ready ? 'System Ready' : 'System Initializing...' }}</span>
      </div>
      <div class="system-banner-meta">
        <span>Chroma {{ componentLabel(systemStatus.components.chroma) }}</span>
        <span>Neo4j {{ componentLabel(systemStatus.components.neo4j) }}</span>
        <span>LLM {{ componentLabel(systemStatus.components.llm) }}</span>
        <span>Celery {{ componentLabel(systemStatus.components.celery) }}</span>
      </div>
    </div>
  </div>

  <el-container class="app-container">
    <el-header class="app-header">
      <div class="header-left">
        <el-icon :size="22"><Promotion /></el-icon>
        <span class="app-title">Legal Agent Workspace</span>
      </div>
      <div class="header-right">
        <span class="user-chip">{{ currentUsername }}</span>
        <el-button
          text
          @click="handleSummary"
          :loading="summaryLoading"
          :disabled="!systemStatus.ready || !currentConversationId"
        >
          <el-icon><Document /></el-icon>Summary
        </el-button>
        <el-button text @click="handleExport" :disabled="!currentConversationId">
          <el-icon><Download /></el-icon>Export
        </el-button>
        <el-button text type="danger" @click="handleClear" :disabled="!currentConversationId">
          <el-icon><Delete /></el-icon>Clear
        </el-button>
        <el-button text type="danger" @click="handleLogout">Logout</el-button>
      </div>
    </el-header>

    <el-container class="main-area">
      <el-aside width="260px" class="side-panel left-panel">
        <SessionList
          :sessions="conversations"
          :current-id="currentConversationId"
          @create="createNewConversation"
          @switch="switchConversation"
          @delete="removeConversation"
        />
      </el-aside>

      <el-main class="chat-area">
        <div v-if="!systemStatus.ready" class="init-overlay">
          <div class="init-card">
            <el-icon class="init-icon rotating"><Loading /></el-icon>
            <div class="init-title">System Initializing...</div>
            <div class="init-subtitle">
              Warming up Chroma, Neo4j, local LLM and Celery. Input is locked until all components are ready.
            </div>
          </div>
        </div>

        <el-scrollbar ref="scrollRef" class="chat-scroll">
          <div class="message-list" ref="messageListRef">
            <div v-if="!messages.length" class="empty-state">
              <div class="empty-title">Start a new legal conversation</div>
              <div class="empty-subtitle">
                Create or select a conversation from the left sidebar, then ask your question.
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
          <el-input
            v-model="inputText"
            type="textarea"
            :rows="2"
            :disabled="!systemStatus.ready || sending || !currentConversationId"
            :placeholder="inputPlaceholder"
            resize="none"
            @keydown="handleKeydown"
          />
          <el-button
            type="primary"
            @click="send"
            :loading="sending"
            :disabled="!systemStatus.ready || !inputText.trim() || !currentConversationId"
          >
            Send
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
  clearConversationMessages,
  createConversation,
  deleteConversation,
  getConversationMessages,
  getConversationSummary,
  getConversations,
  getGraphSubgraph,
  getSystemStatus,
  sendMessage,
  submitFeedback,
} from './api/index.js'
import { clearAuthSession, getStoredUser } from './utils/auth.js'

const router = useRouter()

const conversations = ref([])
const currentConversationId = ref(null)
const currentSessionId = ref('')
const messages = ref([])
const inputText = ref('')
const sending = ref(false)
const summaryLoading = ref(false)
const scrollRef = ref(null)
const messageListRef = ref(null)
const statusTimer = ref(null)
const graphSidebarCollapsed = ref(false)
const latestGraphRequestId = ref(0)

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

const currentUsername = computed(() => getStoredUser()?.username || 'Authenticated User')

const inputPlaceholder = computed(() => {
  if (!systemStatus.ready) return 'System is initializing. Input is temporarily locked.'
  if (!currentConversationId.value) return 'Create or select a conversation first.'
  return 'Enter your legal question. Press Enter to send, Shift+Enter for newline.'
})

onMounted(async () => {
  startSystemStatusPolling()
  await initializeConversations()
})

onUnmounted(() => {
  stopSystemStatusPolling()
})

function componentLabel(value) {
  return value === 'connected' ? 'connected' : 'loading'
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
  }
}

function applyConversationPayload(payload) {
  const conversation = payload?.conversation
  const backendMessages = Array.isArray(payload?.messages) ? payload.messages : []

  if (conversation) {
    currentConversationId.value = conversation.id
    currentSessionId.value = conversation.session_id || ''
  }

  messages.value = backendMessages.map((message, index) => buildMessageViewModel(message, index))
  syncCurrentAnalysisFromMessages()
  scrollBottom()
}

function syncCurrentAnalysisFromMessages() {
  resetAnalysis()
  const latestAssistant = [...messages.value].reverse().find((item) => item.role !== 'user')
  if (!latestAssistant) return
  currentAnalysis.actions = latestAssistant.agentActions || []
  currentAnalysis.sources = latestAssistant.sources || []
}

async function initializeConversations() {
  try {
    await refreshConversationList()
    if (!conversations.value.length) {
      await createNewConversation()
      return
    }

    const firstConversation = conversations.value[0]
    await switchConversation(firstConversation.id)
  } catch (error) {
    console.error('Failed to initialize conversations:', error)
    ElMessage.error('Failed to load conversations.')
  }
}

async function refreshConversationList(preferredConversationId = null) {
  const { data } = await getConversations()
  conversations.value = Array.isArray(data.conversations) ? data.conversations : []

  if (preferredConversationId) {
    const exists = conversations.value.find((item) => item.id === preferredConversationId)
    if (exists) {
      currentConversationId.value = preferredConversationId
      currentSessionId.value = exists.session_id || ''
      return
    }
  }

  if (currentConversationId.value) {
    const active = conversations.value.find((item) => item.id === currentConversationId.value)
    if (active) {
      currentSessionId.value = active.session_id || ''
    }
  }
}

async function createNewConversation() {
  try {
    const { data } = await createConversation({})
    const conversation = data.conversation
    await refreshConversationList(conversation.id)
    await switchConversation(conversation.id)
    ElMessage.success('Conversation created.')
  } catch (error) {
    console.error('Failed to create conversation:', error)
    ElMessage.error('Failed to create conversation.')
  }
}

async function switchConversation(conversationId) {
  if (!conversationId) return
  try {
    resetAnalysis()
    resetGraph()
    const { data } = await getConversationMessages(conversationId)
    applyConversationPayload(data)
  } catch (error) {
    console.error('Failed to load conversation messages:', error)
    ElMessage.error('Failed to load conversation messages.')
  }
}

async function reloadCurrentConversation() {
  if (!currentConversationId.value) return
  await refreshConversationList(currentConversationId.value)
  await switchConversation(currentConversationId.value)
}

async function removeConversation(conversationId) {
  if (!conversationId) return

  ElMessageBox.confirm('Delete this conversation?', 'Warning', { type: 'warning' })
    .then(async () => {
      await deleteConversation(conversationId)
      await refreshConversationList()

      if (!conversations.value.length) {
        currentConversationId.value = null
        currentSessionId.value = ''
        messages.value = []
        await createNewConversation()
      } else {
        const nextId = conversations.value[0].id
        await switchConversation(nextId)
      }

      ElMessage.success('Conversation deleted.')
    })
    .catch(() => {})
}

async function fetchGraph(query) {
  const requestId = ++latestGraphRequestId.value
  currentGraph.query = query
  currentGraph.nodes = []
  currentGraph.edges = []
  currentGraph.tokens = []
  currentGraph.generatedAt = ''
  currentGraph.loading = true
  currentGraph.error = ''

  try {
    const { data } = await getGraphSubgraph(query)
    if (requestId !== latestGraphRequestId.value) return
    currentGraph.query = data.query || query
    currentGraph.nodes = data.nodes || []
    currentGraph.edges = data.edges || []
    currentGraph.tokens = data.tokens || []
    currentGraph.generatedAt = data.generated_at || ''
  } catch (error) {
    if (requestId !== latestGraphRequestId.value) return
    currentGraph.error = error?.response?.data?.detail || 'Failed to load graph data.'
  } finally {
    if (requestId === latestGraphRequestId.value) {
      currentGraph.loading = false
    }
  }
}

async function send() {
  if (!systemStatus.ready) {
    ElMessage.warning('System is still initializing.')
    return
  }

  if (!currentConversationId.value) {
    ElMessage.warning('Create a conversation first.')
    return
  }

  const text = inputText.value.trim()
  if (!text || sending.value) return

  const tempKey = `temp-user-${Date.now()}`
  messages.value.push({
    id: tempKey,
    key: tempKey,
    role: 'user',
    content: text,
    time: formatTime(new Date().toISOString()),
    sources: [],
    agentActions: [],
  })
  inputText.value = ''
  sending.value = true
  graphSidebarCollapsed.value = false
  scrollBottom()
  void fetchGraph(text)

  try {
    await sendMessage(text, currentConversationId.value)
    await reloadCurrentConversation()
  } catch (error) {
    console.error('Failed to send message:', error)
    messages.value.push({
      id: `temp-error-${Date.now()}`,
      key: `temp-error-${Date.now()}`,
      role: 'assistant',
      content: `Request failed: ${error.message || 'Unknown error'}`,
      time: formatTime(new Date().toISOString()),
      sources: [],
      agentActions: [],
    })
  } finally {
    sending.value = false
    scrollBottom()
  }
}

function handleKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    void send()
  }
}

async function handleSummary() {
  if (!currentConversationId.value) return

  summaryLoading.value = true
  try {
    const { data } = await getConversationSummary(currentConversationId.value)
    messages.value.push({
      id: `summary-${Date.now()}`,
      key: `summary-${Date.now()}`,
      role: 'assistant',
      content: `Conversation summary:\n${data.summary}`,
      time: formatTime(new Date().toISOString()),
      sources: [],
      agentActions: [],
    })
    scrollBottom()
    ElMessage.success('Summary generated.')
  } catch (error) {
    console.error('Failed to summarize conversation:', error)
    ElMessage.error('Failed to generate summary.')
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
  link.download = `conversation_${currentConversationId.value}.json`
  link.click()
  URL.revokeObjectURL(link.href)
  ElMessage.success('Conversation exported.')
}

function handleLogout() {
  clearAuthSession()
  ElMessage.success('Logged out.')
  router.replace('/login')
}

function handleClear() {
  if (!currentConversationId.value) return

  ElMessageBox.confirm('Clear all messages in this conversation?', 'Warning', { type: 'warning' })
    .then(async () => {
      await clearConversationMessages(currentConversationId.value)
      await reloadCurrentConversation()
      ElMessage.success('Conversation cleared.')
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
    ElMessage.success('Feedback submitted.')
  } catch (error) {
    console.error('Failed to submit feedback:', error)
    ElMessage.error('Failed to submit feedback.')
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
}

.empty-subtitle {
  margin-top: 8px;
  font-size: 13px;
  line-height: 1.6;
  max-width: 420px;
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

.input-area .el-button {
  height: 54px;
  min-width: 80px;
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

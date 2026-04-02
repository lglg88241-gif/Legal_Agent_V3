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
        <span>LLM {{ componentLabel(systemStatus.components.llm) }}</span>
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
        <el-button text type="danger" @click="handleClear" :disabled="!currentConversationId">
          <el-icon><Delete /></el-icon>清空当前会话
        </el-button>
        <el-button text type="danger" @click="handleLogout">退出登录</el-button>
      </div>
    </el-header>

    <el-container class="main-area">
      <el-aside width="260px" class="side-panel left-panel">
        <SessionList
          :sessions="conversations"
          :current-id="currentConversationId"
          @create="createNewConversation"
          @switch="switchConversation"
          @rename="promptRenameConversation"
          @delete="removeConversation"
        />
      </el-aside>

      <el-main class="chat-area">
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
  getConversationGraph,
  getConversationMessages,
  getConversationSummary,
  getConversations,
  getSystemStatus,
  sendMessage,
  submitFeedback,
  updateConversationTitle,
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

const currentUsername = computed(() => getStoredUser()?.username || '已登录用户')
const currentConversation = computed(() =>
  conversations.value.find((item) => item.id === currentConversationId.value) || null
)

const inputPlaceholder = computed(() => {
  if (!systemStatus.ready) return '系统初始化中，输入框暂时锁定。'
  if (!currentConversationId.value) return '请先创建或选择一个会话。'
  return '请输入法律问题，Enter 发送，Shift + Enter 换行。'
})

onMounted(async () => {
  startSystemStatusPolling()
  await initializeConversations()
})

onUnmounted(() => {
  stopSystemStatusPolling()
})

function componentLabel(value) {
  return value === 'connected' ? '已连接' : '加载中'
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
  const { data } = await getConversations()
  const nextList = Array.isArray(data.conversations) ? data.conversations : []
  setConversations(nextList)

  const activeId = preferredConversationId || currentConversationId.value
  const active = conversations.value.find((item) => item.id === activeId)
  if (active) {
    currentConversationId.value = active.id
    currentSessionId.value = active.session_id || ''
  }
}

async function createNewConversation(options = {}) {
  try {
    const { data } = await createConversation({})
    const conversation = data.conversation
    upsertConversationLocal(conversation)
    await switchConversation(conversation.id)
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
  try {
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

async function removeConversation(conversationId) {
  if (!conversationId) return

  ElMessageBox.confirm('确定删除该会话及其全部消息吗？', '删除确认', { type: 'warning' })
    .then(async () => {
      try {
        await deleteConversation(conversationId)
        removeConversationLocal(conversationId)

        if (conversationId === currentConversationId.value) {
          const nextConversation = conversations.value[0]
          if (nextConversation) {
            await switchConversation(nextConversation.id)
          } else {
            currentConversationId.value = null
            currentSessionId.value = ''
            messages.value = []
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

async function fetchConversationGraph(conversationId, query = '') {
  if (!conversationId) {
    resetGraph()
    return
  }

  const requestId = ++latestGraphRequestId.value
  currentGraph.query = query || currentConversation.value?.title || `会话 ${conversationId} 案情图谱`
  currentGraph.nodes = []
  currentGraph.edges = []
  currentGraph.tokens = []
  currentGraph.generatedAt = ''
  currentGraph.loading = true
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
    if (requestId === latestGraphRequestId.value) {
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

function updatePendingMessage(tempId, patch = {}) {
  const target = messages.value.find((item) => item.id === tempId)
  if (!target) return
  Object.assign(target, patch)
}

async function send() {
  if (!systemStatus.ready) {
    ElMessage.warning('系统仍在初始化，请稍候。')
    return
  }

  if (!currentConversationId.value) {
    ElMessage.warning('请先创建或选择一个会话。')
    return
  }

  const text = inputText.value.trim()
  if (!text || sending.value) return

  const conversationId = currentConversationId.value
  const tempKey = `temp-user-${Date.now()}`
  const nowIso = new Date().toISOString()

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
  touchConversationLocal(conversationId)
  void ensureConversationTitle(text)
  currentGraph.loading = true
  currentGraph.error = ''
  currentGraph.query = text
  scrollBottom()

  try {
    const { data } = await sendMessage(text, conversationId)
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
    await refreshConversationList(conversationId)
    await fetchConversationGraph(conversationId, text)
  } catch (error) {
    console.error('Failed to send message:', error)
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

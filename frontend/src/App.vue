<template>
  <div class="system-banner" :class="systemStatus.ready ? 'ready' : 'loading'">
    <div class="system-banner-content">
      <div class="system-banner-main">
        <el-icon v-if="systemStatus.ready" class="banner-icon"><CircleCheckFilled /></el-icon>
        <el-icon v-else class="banner-icon rotating"><Loading /></el-icon>
        <span>{{ systemStatus.ready ? '系统就绪' : '系统初始化中...' }}</span>
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
        <span class="app-title">智能法律咨询系统</span>
      </div>
      <div class="header-right">
        <el-button text @click="handleSummary" :loading="summaryLoading" :disabled="!systemStatus.ready">
          <el-icon><Document /></el-icon>对话总结
        </el-button>
        <el-button text @click="handleExport">
          <el-icon><Download /></el-icon>导出对话
        </el-button>
        <el-button text type="danger" @click="handleClear">
          <el-icon><Delete /></el-icon>清空
        </el-button>
      </div>
    </el-header>

    <el-container class="main-area">
      <el-aside width="220px" class="side-panel left-panel">
        <SessionList
          :sessions="sessions"
          :current-id="currentSessionId"
          @create="createSession"
          @switch="switchSession"
          @delete="deleteSession"
        />
      </el-aside>

      <el-main class="chat-area">
        <div v-if="!systemStatus.ready" class="init-overlay">
          <div class="init-card">
            <el-icon class="init-icon rotating"><Loading /></el-icon>
            <div class="init-title">系统初始化中...</div>
            <div class="init-subtitle">正在预热向量库、Neo4j、本地模型与 Celery，请稍候。</div>
          </div>
        </div>

        <el-scrollbar ref="scrollRef" class="chat-scroll">
          <div class="message-list" ref="messageListRef">
            <ChatMessage v-for="(m, i) in messages" :key="i" :msg="m" />
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
            :disabled="!systemStatus.ready || sending"
            :placeholder="systemStatus.ready ? '请输入您的问题，Enter 发送，Shift+Enter 换行' : '系统初始化中，输入框暂时锁定...'"
            resize="none"
            @keydown="handleKeydown"
          />
          <el-button
            type="primary"
            @click="send"
            :loading="sending"
            :disabled="!systemStatus.ready || !inputText.trim()"
          >
            发送
          </el-button>
        </div>
      </el-main>

      <el-aside width="260px" class="side-panel right-panel">
        <AnalysisPanel
          :status="systemStatus"
          :analysis="currentAnalysis"
          @feedback="handleFeedback"
        />
      </el-aside>
    </el-container>
  </el-container>
</template>

<script setup>
import { nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import AnalysisPanel from './components/AnalysisPanel.vue'
import ChatMessage from './components/ChatMessage.vue'
import SessionList from './components/SessionList.vue'
import {
  getSummary,
  getSystemStatus,
  resetDialog,
  sendMessage,
  submitFeedback,
} from './api/index.js'

const messages = ref([])
const inputText = ref('')
const sending = ref(false)
const summaryLoading = ref(false)
const sessions = ref([])
const currentSessionId = ref('')
const scrollRef = ref(null)
const messageListRef = ref(null)
const turnIndex = ref(0)
const statusTimer = ref(null)

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

onMounted(() => {
  loadSessions()
  startSystemStatusPolling()
})

onUnmounted(() => {
  stopSystemStatusPolling()
})

function componentLabel(value) {
  return value === 'connected' ? 'connected' : 'loading'
}

function startSystemStatusPolling() {
  refreshSystemStatus()
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
  } catch {
    systemStatus.status = 'loading'
    systemStatus.ready = false
    systemStatus.healthy = false
  }
}

function genId() {
  return 'session_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8)
}

function loadSessions() {
  const stored = localStorage.getItem('vue_sessions')
  if (stored) {
    sessions.value = JSON.parse(stored)
    if (sessions.value.length && !currentSessionId.value) {
      switchSession(sessions.value[0].id)
    }
  } else {
    createSession()
  }
}

function saveSessions() {
  localStorage.setItem('vue_sessions', JSON.stringify(sessions.value))
}

function createSession() {
  const nowTs = Date.now()
  const session = {
    id: genId(),
    name: `会话 ${new Date(nowTs).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`,
    lastActive: nowTs,
  }
  sessions.value.unshift(session)
  saveSessions()
  switchSession(session.id)
}

function switchSession(id) {
  if (id === currentSessionId.value) return
  currentSessionId.value = id
  messages.value = []
  turnIndex.value = 0
  resetAnalysis()

  const stored = localStorage.getItem(`chat_${id}`)
  if (stored) {
    messages.value = JSON.parse(stored)
    turnIndex.value = messages.value.filter((m) => m.role === 'user').length
  }

  if (!messages.value.length) pushWelcome()
  scrollBottom()
}

function deleteSession(id) {
  ElMessageBox.confirm('确定删除此会话？', '提示', { type: 'warning' })
    .then(() => {
      sessions.value = sessions.value.filter((s) => s.id !== id)
      localStorage.removeItem(`chat_${id}`)
      saveSessions()
      if (id === currentSessionId.value) {
        if (sessions.value.length) switchSession(sessions.value[0].id)
        else createSession()
      }
      ElMessage.success('已删除')
    })
    .catch(() => {})
}

function pushWelcome() {
  messages.value.push({
    role: 'bot',
    content:
      '您好！我是智能法律咨询助手，可以为您提供法律条文、类案检索和综合分析建议。系统初始化完成后，您即可开始提问。',
    time: now(),
  })
}

function now() {
  return new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

async function send() {
  if (!systemStatus.ready) {
    ElMessage.warning('系统仍在初始化，请稍候。')
    return
  }

  const text = inputText.value.trim()
  if (!text || sending.value) return

  messages.value.push({ role: 'user', content: text, time: now() })
  inputText.value = ''
  sending.value = true
  scrollBottom()

  try {
    const { data } = await sendMessage(text, currentSessionId.value)
    const botMsg = {
      role: 'bot',
      content: data.response,
      time: now(),
      sources: data.sources || [],
      agentActions: data.agent_actions || [],
    }
    messages.value.push(botMsg)
    turnIndex.value += 1
    currentAnalysis.actions = data.agent_actions || []
    currentAnalysis.sources = data.sources || []

    const session = sessions.value.find((item) => item.id === currentSessionId.value)
    if (session) {
      session.lastActive = Date.now()
      saveSessions()
    }

    localStorage.setItem(`chat_${currentSessionId.value}`, JSON.stringify(messages.value))
  } catch (err) {
    messages.value.push({
      role: 'bot',
      content: `抱歉，请求失败，请检查后端服务是否启动。\n错误信息：${err.message || '未知错误'}`,
      time: now(),
    })
  } finally {
    sending.value = false
    scrollBottom()
  }
}

function handleKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    send()
  }
}

function resetAnalysis() {
  currentAnalysis.actions = []
  currentAnalysis.sources = []
}

async function handleSummary() {
  summaryLoading.value = true
  try {
    const { data } = await getSummary(currentSessionId.value)
    messages.value.push({ role: 'bot', content: `对话总结：\n${data.summary}`, time: now() })
    scrollBottom()
    ElMessage.success('总结已生成')
  } catch {
    ElMessage.error('生成总结失败')
  } finally {
    summaryLoading.value = false
  }
}

function handleExport() {
  const blob = new Blob(
    [JSON.stringify({ session_id: currentSessionId.value, messages: messages.value }, null, 2)],
    { type: 'application/json' }
  )
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = `对话记录_${new Date().toISOString().slice(0, 10)}.json`
  link.click()
  URL.revokeObjectURL(link.href)
  ElMessage.success('已导出')
}

function handleClear() {
  ElMessageBox.confirm('确定清空当前对话？', '提示', { type: 'warning' })
    .then(async () => {
      messages.value = []
      localStorage.removeItem(`chat_${currentSessionId.value}`)
      turnIndex.value = 0
      resetAnalysis()
      try {
        await resetDialog(currentSessionId.value)
      } catch {
        // Ignore reset errors for local cleanup.
      }
      pushWelcome()
      ElMessage.success('已清空')
    })
    .catch(() => {})
}

async function handleFeedback({ rating, comment }) {
  try {
    await submitFeedback({
      session_id: currentSessionId.value,
      turn_index: turnIndex.value,
      rating,
      comment,
      feedback_type: 'general',
    })
    ElMessage.success('感谢您的反馈')
  } catch {
    ElMessage.error('提交失败')
  }
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
  width: min(1200px, calc(100vw - 24px));
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
  gap: 4px;
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

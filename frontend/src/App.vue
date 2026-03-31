<template>
  <el-container class="app-container">
    <!-- 顶部栏 -->
    <el-header class="app-header">
      <div class="header-left">
        <el-icon :size="22"><Promotion /></el-icon>
        <span class="app-title">智能领域聊天机器人</span>
      </div>
      <div class="header-right">
        <el-button text @click="handleSummary" :loading="summaryLoading">
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
      <!-- 左侧：会话列表 -->
      <el-aside width="220px" class="side-panel left-panel">
        <SessionList
          :sessions="sessions"
          :current-id="currentSessionId"
          @create="createSession"
          @switch="switchSession"
          @delete="deleteSession"
        />
      </el-aside>

      <!-- 中间：聊天区 -->
      <el-main class="chat-area">
        <el-scrollbar ref="scrollRef" class="chat-scroll">
          <div class="message-list" ref="messageListRef">
            <ChatMessage v-for="(m, i) in messages" :key="i" :msg="m" />
            <!-- 加载中动画 -->
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

        <!-- 输入框 -->
        <div class="input-area">
          <el-input
            v-model="inputText"
            type="textarea"
            :rows="2"
            placeholder="请输入您的问题，Enter 发送，Shift+Enter 换行"
            resize="none"
            @keydown="handleKeydown"
          />
          <el-button type="primary" @click="send" :loading="sending" :disabled="!inputText.trim()">
            发送
          </el-button>
        </div>
      </el-main>

      <!-- 右侧：分析面板 -->
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
import { ref, reactive, nextTick, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import ChatMessage from './components/ChatMessage.vue'
import SessionList from './components/SessionList.vue'
import AnalysisPanel from './components/AnalysisPanel.vue'
import {
  healthCheck,
  sendMessage,
  resetDialog,
  getSummary,
  submitFeedback,
  getKnowledgeStatus,
} from './api/index.js'

// ========== 状态 ==========
const messages = ref([])
const inputText = ref('')
const sending = ref(false)
const summaryLoading = ref(false)
const sessions = ref([])
const currentSessionId = ref('')
const scrollRef = ref(null)
const messageListRef = ref(null)
const turnIndex = ref(0)

const systemStatus = reactive({ healthy: false, ragReady: false, docCount: 0, model: '', tools: [] })
const currentAnalysis = reactive({ actions: [], sources: [] })

// ========== 初始化 ==========
onMounted(() => {
  loadSessions()
  checkHealth()
  setInterval(checkHealth, 30000)
})

async function checkHealth() {
  try {
    const { data } = await healthCheck()
    systemStatus.healthy = data.status === 'online'
    systemStatus.model = data.model || ''
    systemStatus.tools = data.tools || []
  } catch {
    systemStatus.healthy = false
  }
  try {
    const { data } = await getKnowledgeStatus()
    systemStatus.ragReady = data.status === 'online' && data.loaded === true
    systemStatus.docCount = data.doc_count || 0
  } catch {
    systemStatus.ragReady = false
  }
}

// ========== 会话管理 ==========
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
  const now = Date.now()
  const s = {
    id: genId(),
    name: `会话 ${new Date(now).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`,
    lastActive: now,
  }
  sessions.value.unshift(s)
  saveSessions()
  switchSession(s.id)
}

function switchSession(id) {
  if (id === currentSessionId.value) return
  currentSessionId.value = id
  messages.value = []
  turnIndex.value = 0
  resetAnalysis()
  // 加载本地历史
  const stored = localStorage.getItem(`chat_${id}`)
  if (stored) {
    messages.value = JSON.parse(stored)
    turnIndex.value = messages.value.filter((m) => m.role === 'user').length
  }
  if (!messages.value.length) pushWelcome()
  scrollBottom()
}

function deleteSession(id) {
  ElMessageBox.confirm('确定删除此会话？', '提示', { type: 'warning' }).then(() => {
    sessions.value = sessions.value.filter((s) => s.id !== id)
    localStorage.removeItem(`chat_${id}`)
    saveSessions()
    if (id === currentSessionId.value) {
      if (sessions.value.length) switchSession(sessions.value[0].id)
      else createSession()
    }
    ElMessage.success('已删除')
  }).catch(() => {})
}

// ========== 聊天 ==========
function pushWelcome() {
  messages.value.push({
    role: 'bot',
    content: '您好！我是智能领域聊天机器人，可以回答您的各种问题。\n\n我在法律方面配备了专业的知识库工具，能提供准确的法律咨询。当然，您也可以问我任何其他问题——日常聊天、常识百科、技术编程等，我都可以帮您！',
    time: now(),
  })
}

function now() {
  return new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

async function send() {
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
    turnIndex.value++

    // 更新右侧分析面板
    currentAnalysis.actions = data.agent_actions || []
    currentAnalysis.sources = data.sources || []

    // 更新会话信息
    const s = sessions.value.find((s) => s.id === currentSessionId.value)
    if (s) { s.lastActive = Date.now(); saveSessions() }

    // 保存到本地
    localStorage.setItem(`chat_${currentSessionId.value}`, JSON.stringify(messages.value))
  } catch (err) {
    messages.value.push({
      role: 'bot',
      content: '抱歉，请求失败，请检查后端服务是否启动。\n错误信息：' + (err.message || '未知错误'),
      time: now(),
    })
  } finally {
    sending.value = false
    scrollBottom()
  }
}

function handleKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

// ========== 分析 ==========
function resetAnalysis() {
  currentAnalysis.actions = []
  currentAnalysis.sources = []
}

// ========== 工具栏 ==========
async function handleSummary() {
  summaryLoading.value = true
  try {
    const { data } = await getSummary(currentSessionId.value)
    messages.value.push({ role: 'bot', content: `📋 对话总结：\n${data.summary}`, time: now() })
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
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `对话记录_${new Date().toISOString().slice(0, 10)}.json`
  a.click()
  URL.revokeObjectURL(a.href)
  ElMessage.success('已导出')
}

function handleClear() {
  ElMessageBox.confirm('确定清空当前对话？', '提示', { type: 'warning' }).then(async () => {
    messages.value = []
    localStorage.removeItem(`chat_${currentSessionId.value}`)
    turnIndex.value = 0
    resetAnalysis()
    try { await resetDialog(currentSessionId.value) } catch { /* 忽略 */ }
    pushWelcome()
    ElMessage.success('已清空')
  }).catch(() => {})
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

// ========== 滚动 ==========
function scrollBottom() {
  nextTick(() => {
    if (scrollRef.value) {
      scrollRef.value.setScrollTop(messageListRef.value?.scrollHeight || 99999)
    }
  })
}
</script>

<style>
/* 全局重置 */
html, body, #app {
  margin: 0;
  padding: 0;
  height: 100%;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
}
</style>

<style scoped>
.app-container {
  height: 100vh;
  background: #f5f7fa;
}

/* 顶部栏 */
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

/* 主体区域 */
.main-area {
  height: calc(100vh - 56px);
}

/* 侧边栏 */
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

/* 聊天区 */
.chat-area {
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

/* 输入区 */
.input-area {
  display: flex;
  gap: 12px;
  padding: 12px 24px 16px;
  background: #fff;
  border-top: 1px solid #e4e7ed;
  align-items: flex-end;
}
.input-area .el-textarea {
  flex: 1;
}
.input-area .el-button {
  height: 54px;
  min-width: 80px;
}

/* 加载动画 */
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
.dot:nth-child(1) { animation-delay: 0s; }
.dot:nth-child(2) { animation-delay: 0.2s; }
.dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes bounce {
  0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
  40% { transform: scale(1); opacity: 1; }
}
</style>

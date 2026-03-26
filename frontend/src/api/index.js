/**
 * API 接口封装（v3.0 Agent 架构 - 高并发异步轮询版）
 */
import axios from 'axios'

const api = axios.create({
  baseURL: 'http://localhost:8000',
  // 在有了异步轮询架构后，其实单个请求不会卡这么久了，
  // 但为了兼容其他非异步的耗时接口，我们依然保持 60s
  timeout: 60000, 
  headers: { 'Content-Type': 'application/json' },
})

// ================= 健康检查 =================
export const healthCheck = () => api.get('/health')

// ================= 核心对话 (架构重构点) =================
// 使用长轮询策略(Polling)适配后端的 Celery 消息队列，实现无感异步化
export const sendMessage = async (userInput, sessionId) => {
  try {
    // 1. 发送对话请求，后端（FastAPI）会秒回一个 Task ID (取餐码)
    const initRes = await api.post('/dialog', { user_input: userInput, session_id: sessionId })
    const taskId = initRes.data.task_id

    // 如果后端直接返回了结果（兼容旧版或降级模式），直接抛给 Vue 组件
    if (!taskId) {
      return initRes
    }

    console.log(`🚀 [Agent] 任务已推入队列，Task ID: ${taskId}。开始后台无感轮询...`)

    // 2. 将轮询逻辑封装为 Promise，完美欺骗上层 UI 组件
    return new Promise((resolve, reject) => {
      const timer = setInterval(async () => {
        try {
          // 拿着取餐码去问进度
          const statusRes = await api.get(`/dialog/status/${taskId}`)
          const statusData = statusRes.data

          if (statusData.status === 'completed') {
            // ✅ 任务完成，组装回 Axios 标准的 { data: ... } 格式返回
            clearInterval(timer)
            console.log(`✅ [Agent] 任务 ${taskId} 执行完毕！`, statusData.data)
            resolve({ data: statusData.data }) 
          } else if (statusData.status === 'failed') {
            // ❌ 任务失败，停止轮询并抛出异常
            clearInterval(timer)
            console.error(`❌ [Agent] 任务失败:`, statusData.error)
            reject(new Error(statusData.error || 'Agent 内部推理出错'))
          }
          // 如果是 processing/pending，什么都不做，1.5秒后继续问
        } catch (pollError) {
          clearInterval(timer)
          console.error('⚠️ [Agent] 轮询请求发生网络异常:', pollError)
          reject(pollError)
        }
      }, 1500) // 每 1.5 秒轮询一次，保护后端性能
    })
  } catch (error) {
    console.error('⚠️ [Agent] 对话请求发送失败:', error)
    throw error
  }
}

// ================= 其他接口保持原样 =================
// 重置对话
export const resetDialog = (sessionId) => api.delete(`/dialog/${sessionId}`)

// 对话历史
export const getHistory = (sessionId, limit = 50) =>
  api.get(`/dialog/${sessionId}/history`, { params: { limit } })

// 对话总结
export const getSummary = (sessionId) =>
  api.post(`/dialog/${sessionId}/summary`)

// 反馈
export const submitFeedback = (data) => api.post('/feedback', data)

// RAG 知识库
export const getKnowledgeStatus = () => api.get('/knowledge/status')
export const searchKnowledge = (query, topK = 5, domain = null) =>
  api.post('/knowledge/search', { query, top_k: topK, domain })

// Agent 状态
export const getAgentStatus = () => api.get('/agent/status')

export default api
import axios from 'axios'

const api = axios.create({
  baseURL: 'http://localhost:8000',
  timeout: 60000,
  headers: { 'Content-Type': 'application/json' },
})

export const healthCheck = () => api.get('/health')
export const getSystemStatus = () => api.get('/system/status')
export const getKnowledgeStatus = () => api.get('/knowledge/status')
export const getGraphSubgraph = (query) => api.get('/graph/subgraph', { params: { query } })
export const searchKnowledge = (query, topK = 5, domain = null) =>
  api.post('/knowledge/search', { query, top_k: topK, domain })
export const getAgentStatus = () => api.get('/agent/status')
export const resetDialog = (sessionId) => api.delete(`/dialog/${sessionId}`)
export const getHistory = (sessionId, limit = 50) =>
  api.get(`/dialog/${sessionId}/history`, { params: { limit } })
export const getSummary = (sessionId) => api.post(`/dialog/${sessionId}/summary`)
export const submitFeedback = (data) => api.post('/feedback', data)

export const sendMessage = async (userInput, sessionId) => {
  const initRes = await api.post('/dialog', { user_input: userInput, session_id: sessionId })
  const taskId = initRes.data.task_id

  if (!taskId) {
    return initRes
  }

  return new Promise((resolve, reject) => {
    const timer = setInterval(async () => {
      try {
        const statusRes = await api.get(`/dialog/status/${taskId}`)
        const statusData = statusRes.data

        if (statusData.status === 'completed') {
          clearInterval(timer)
          resolve({ data: statusData.data })
        } else if (statusData.status === 'failed') {
          clearInterval(timer)
          reject(new Error(statusData.error || 'Agent 内部推理出错'))
        }
      } catch (pollError) {
        clearInterval(timer)
        reject(pollError)
      }
    }, 1500)
  })
}

export default api

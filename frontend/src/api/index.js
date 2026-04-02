import axios from 'axios'
import { clearAuthSession, getToken } from '../utils/auth.js'

function formatValidationDetail(detail) {
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object') {
          const path = Array.isArray(item.loc) ? item.loc.join('.') : ''
          const message = item.msg || '请求校验失败'
          return path ? `${path}: ${message}` : message
        }
        return '请求校验失败'
      })
      .join('\n')
  }

  if (typeof detail === 'string') {
    return detail
  }

  return '请求校验失败'
}

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000',
  timeout: 60000,
  headers: { 'Content-Type': 'application/json' },
})

api.interceptors.request.use(
  (config) => {
    const token = getToken()
    if (token) {
      config.headers = config.headers || {}
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error)
)

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error?.response?.status === 401) {
      clearAuthSession()
      if (window.location.hash !== '#/login') {
        window.location.hash = '#/login'
      }
    }

    if (error?.response?.status === 422) {
      const message = formatValidationDetail(error.response?.data?.detail)
      window.alert(message)
    }

    return Promise.reject(error)
  }
)

export const register = (username, password) =>
  api.post('/auth/register', { username, password })

export const login = (username, password) =>
  api.post('/auth/login', { username, password })

export const healthCheck = () => api.get('/health')
export const getSystemStatus = () => api.get('/system/status')
export const getKnowledgeStatus = () => api.get('/knowledge/status')
export const getGraphSubgraph = (query) => api.get('/graph/subgraph', { params: { query } })
export const getConversationGraph = (conversationId) =>
  api.get(`/conversations/${conversationId}/graph`)
export const searchKnowledge = (query, topK = 5, domain = null) =>
  api.post('/knowledge/search', { query, top_k: topK, domain })
export const getAgentStatus = () => api.get('/agent/status')
export const submitFeedback = (data) => api.post('/feedback', data)

export const createConversation = (payload = {}) => api.post('/conversations', payload)
export const getConversations = () => api.get('/conversations')
export const updateConversationTitle = (conversationId, title) =>
  api.put(`/conversations/${conversationId}`, { title })
export const getConversationMessages = (conversationId) =>
  api.get(`/conversations/${conversationId}/messages`)
export const deleteConversation = (conversationId) =>
  api.delete(`/conversations/${conversationId}`)
export const clearConversationMessages = (conversationId) =>
  api.delete(`/conversations/${conversationId}/messages`)
export const getConversationSummary = (conversationId) =>
  api.post(`/conversations/${conversationId}/summary`)

export const sendMessage = async (userInput, conversationId) => {
  const initRes = await api.post('/dialog', {
    user_input: userInput,
    conversation_id: conversationId,
  })
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
          reject(new Error(statusData.error || '智能体执行失败'))
        }
      } catch (pollError) {
        clearInterval(timer)
        reject(pollError)
      }
    }, 1500)
  })
}

export default api

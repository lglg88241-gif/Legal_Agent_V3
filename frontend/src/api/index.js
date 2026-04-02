import axios from 'axios'
import { clearAuthSession, getToken } from '../utils/auth.js'

function formatValidationDetail(detail) {
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object') {
          const path = Array.isArray(item.loc) ? item.loc.join('.') : ''
          const message = item.msg || 'Validation error'
          return path ? `${path}: ${message}` : message
        }
        return 'Validation error'
      })
      .join('\n')
  }

  if (typeof detail === 'string') {
    return detail
  }

  return 'Request validation failed'
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
          reject(new Error(statusData.error || 'Agent internal error'))
        }
      } catch (pollError) {
        clearInterval(timer)
        reject(pollError)
      }
    }, 1500)
  })
}

export default api

export const TOKEN_KEY = 'access_token'
export const USER_KEY = 'auth_user'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function getStoredUser() {
  const raw = localStorage.getItem(USER_KEY)
  if (!raw) return null

  try {
    return JSON.parse(raw)
  } catch {
    return null
  }
}

export function setAuthSession(payload) {
  const token = payload?.access_token || ''
  const user = payload?.user || null

  if (token) {
    localStorage.setItem(TOKEN_KEY, token)
  }

  if (user) {
    localStorage.setItem(USER_KEY, JSON.stringify(user))
  }
}

export function clearAuthSession() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

export function isAuthenticated() {
  return Boolean(getToken())
}

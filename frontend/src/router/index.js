import { createRouter, createWebHashHistory } from 'vue-router'
import ChatView from '../App.vue'
import LoginView from '../views/LoginView.vue'
import { isAuthenticated } from '../utils/auth.js'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/',
      redirect: () => (isAuthenticated() ? '/chat' : '/login'),
    },
    {
      path: '/login',
      name: 'login',
      component: LoginView,
      meta: { requiresAuth: false },
    },
    {
      path: '/chat',
      name: 'chat',
      component: ChatView,
      meta: { requiresAuth: true },
    },
  ],
})

router.beforeEach((to) => {
  const hasToken = isAuthenticated()

  if (to.meta.requiresAuth && !hasToken) {
    return {
      path: '/login',
      query: { redirect: to.fullPath },
    }
  }

  if (to.path === '/login' && hasToken) {
    return '/chat'
  }

  return true
})

export default router

import { createRouter, createWebHistory } from 'vue-router'

import { getToken } from '@/api/auth'
import ChatView from '@/views/ChatView.vue'
import LoginView from '@/views/LoginView.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', name: 'chat', component: ChatView, meta: { requiresAuth: true } },
    { path: '/login', name: 'login', component: LoginView },
  ],
})

// 全局守卫：未登录只能访问 /login，已登录不再进入 /login
router.beforeEach((to) => {
  const loggedIn = Boolean(getToken())
  if (to.meta.requiresAuth && !loggedIn) {
    return { name: 'login' }
  }
  if (to.name === 'login' && loggedIn) {
    return { name: 'chat' }
  }
  return true
})

export default router

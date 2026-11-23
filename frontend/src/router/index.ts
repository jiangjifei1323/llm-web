import { createRouter, createWebHistory } from 'vue-router'

import { fetchMe, getToken, isAdmin } from '@/api/auth'
import AdminView from '@/views/AdminView.vue'
import ChatView from '@/views/ChatView.vue'
import LoginView from '@/views/LoginView.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', name: 'chat', component: ChatView, meta: { requiresAuth: true } },
    { path: '/login', name: 'login', component: LoginView },
    {
      path: '/admin',
      name: 'admin',
      component: AdminView,
      meta: { requiresAuth: true, requiresAdmin: true },
    },
  ],
})

// 全局守卫：未登录只能访问 /login，已登录不再进入 /login，后台管理仅管理员可访问
router.beforeEach(async (to) => {
  const loggedIn = Boolean(getToken())
  if (to.meta.requiresAuth && !loggedIn) {
    return { name: 'login' }
  }
  if (to.name === 'login' && loggedIn) {
    return { name: 'chat' }
  }
  if (to.meta.requiresAdmin && loggedIn && !isAdmin()) {
    // 本地标识缺失或过期（例如旧登录态、权限变更）时向后端确认一次
    try {
      await fetchMe()
    } catch {
      return { name: 'chat' }
    }
  }
  if (to.meta.requiresAdmin && !isAdmin()) {
    return { name: 'chat' }
  }
  return true
})

export default router

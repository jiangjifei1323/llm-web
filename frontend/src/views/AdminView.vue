<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import UserManagement from '@/components/UserManagement.vue'
import { clearAuth, getUsername } from '@/api/auth'

const router = useRouter()
const username = ref(getUsername() ?? '')

type MenuKey = 'settings' | 'users'

interface MenuNode {
  key: MenuKey
  label: string
  /** 下级菜单；有子项时父项渲染为可展开分组 */
  children?: MenuNode[]
}

/** 后台功能菜单；新增菜单只需往树里追加一项，并补对应的内容分支。 */
const menus: MenuNode[] = [
  { key: 'settings', label: '系统设置', children: [{ key: 'users', label: '用户管理' }] },
]

const activeKey = ref<MenuKey>('settings')
// 默认展开「系统设置」分组，用户管理一眼可见
const defaultOpeneds = ['settings']

function findMenu(key: MenuKey): MenuNode | undefined {
  for (const menu of menus) {
    if (menu.key === key) return menu
    const child = menu.children?.find((item) => item.key === key)
    if (child) return child
  }
  return undefined
}

const activeLabel = computed(() => findMenu(activeKey.value)?.label ?? '')

/** el-menu 选中叶子项时回调，index 即菜单 key。 */
function selectMenu(index: string) {
  activeKey.value = index as MenuKey
}

function backToChat() {
  void router.push({ name: 'chat' })
}

function logout() {
  clearAuth()
  void router.replace({ name: 'login' })
}
</script>

<template>
  <div class="admin-layout">
    <aside class="admin-aside">
      <h2 class="aside-title">后台管理</h2>

      <el-menu
        :default-active="activeKey"
        :default-openeds="defaultOpeneds"
        class="side-menu"
        @select="selectMenu"
      >
        <template v-for="menu in menus" :key="menu.key">
          <el-sub-menu v-if="menu.children && menu.children.length" :index="menu.key">
            <template #title>
              <el-icon><Setting /></el-icon>
              <span>{{ menu.label }}</span>
            </template>
            <el-menu-item v-for="child in menu.children" :key="child.key" :index="child.key">
              {{ child.label }}
            </el-menu-item>
          </el-sub-menu>
          <el-menu-item v-else :index="menu.key">
            <span>{{ menu.label }}</span>
          </el-menu-item>
        </template>
      </el-menu>

      <div class="aside-footer">
        <span class="aside-user">{{ username }}</span>
        <el-button type="danger" link size="small" @click="logout">退出登录</el-button>
      </div>
    </aside>

    <div class="admin-main">
      <header class="admin-header">
        <div>
          <h1>{{ activeLabel }}</h1>
          <p class="subtitle">仅管理员可见的后台配置区域</p>
        </div>
        <el-button plain type="primary" @click="backToChat">返回聊天</el-button>
      </header>

      <section v-if="activeKey === 'settings'" class="panel">
        <h3 class="panel-title">系统设置</h3>
        <p class="panel-empty">该菜单已就位，具体配置项待接入。</p>
      </section>

      <UserManagement v-else-if="activeKey === 'users'" />
    </div>
  </div>
</template>

<style scoped>
.admin-layout {
  display: flex;
  height: 100vh;
  font-family: -apple-system, "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
}

/* ---------- 左侧菜单 ---------- */
.admin-aside {
  display: flex;
  flex-direction: column;
  width: 220px;
  flex-shrink: 0;
  padding: 16px 12px;
  border-right: 1px solid #e5e5e5;
  background: #f7f7f7;
  box-sizing: border-box;
}

.aside-title {
  margin: 0 0 14px;
  color: #333;
  font-size: 16px;
  font-weight: 600;
}

.side-menu {
  flex: 1;
  border-right: none;
  background: transparent;
}

.side-menu :deep(.el-menu-item.is-active) {
  background: #e3f2e6;
  font-weight: 600;
}

.aside-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid #e5e5e5;
}

.aside-user {
  max-width: 110px;
  overflow: hidden;
  color: #666;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ---------- 右侧内容 ---------- */
.admin-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 18px 24px;
  background: #fafafa;
  box-sizing: border-box;
}

.admin-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.admin-header h1 {
  margin: 0 0 4px;
  color: #333;
  font-size: 20px;
}

.subtitle {
  margin: 0;
  color: #888;
  font-size: 13px;
}

.panel {
  padding: 18px 20px;
  border: 1px solid #e5e5e5;
  border-radius: 10px;
  background: #fff;
}

.panel-title {
  margin: 0 0 8px;
  color: #333;
  font-size: 15px;
  font-weight: 600;
}

.panel-empty {
  margin: 0;
  color: #888;
  font-size: 13px;
  line-height: 1.6;
}
</style>

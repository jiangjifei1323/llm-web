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
  /** 下级菜单；有子项时父项右侧显示展开箭头 */
  children?: MenuNode[]
}

/** 后台功能菜单；新增菜单只需往树里追加一项，并补对应的内容分支。 */
const menus: MenuNode[] = [
  { key: 'settings', label: '系统设置', children: [{ key: 'users', label: '用户管理' }] },
]

const activeKey = ref<MenuKey>('settings')
// 默认展开「系统设置」分组，用户管理一眼可见
const openGroups = ref<Record<string, boolean>>({ settings: true })

function findMenu(key: MenuKey): MenuNode | undefined {
  for (const menu of menus) {
    if (menu.key === key) return menu
    const child = menu.children?.find((item) => item.key === key)
    if (child) return child
  }
  return undefined
}

const activeLabel = computed(() => findMenu(activeKey.value)?.label ?? '')

/** 点父项：切换内容并展开/收起其下级菜单；点子项：只切换内容。 */
function selectMenu(node: MenuNode) {
  activeKey.value = node.key
  if (node.children) {
    openGroups.value[node.key] = !openGroups.value[node.key]
  }
}

function isGroupOpen(node: MenuNode): boolean {
  return Boolean(node.children && openGroups.value[node.key])
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
      <ul class="menu-list">
        <li v-for="menu in menus" :key="menu.key">
          <button
            class="menu-item"
            :class="{ active: menu.key === activeKey }"
            @click="selectMenu(menu)"
          >
            <span class="menu-label">{{ menu.label }}</span>
            <span v-if="menu.children" class="menu-caret">{{ isGroupOpen(menu) ? '▾' : '▸' }}</span>
          </button>
          <ul v-if="isGroupOpen(menu)" class="submenu-list">
            <li v-for="child in menu.children" :key="child.key">
              <button
                class="menu-item submenu"
                :class="{ active: child.key === activeKey }"
                @click="selectMenu(child)"
              >
                <span class="menu-label">{{ child.label }}</span>
              </button>
            </li>
          </ul>
        </li>
      </ul>

      <div class="aside-footer">
        <span class="aside-user">{{ username }}</span>
        <button class="link-btn" @click="logout">退出登录</button>
      </div>
    </aside>

    <div class="admin-main">
      <header class="admin-header">
        <div>
          <h1>{{ activeLabel }}</h1>
          <p class="subtitle">仅管理员可见的后台配置区域</p>
        </div>
        <button class="back-btn" @click="backToChat">返回聊天</button>
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
  width: 200px;
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

.menu-list {
  flex: 1;
  margin: 0;
  padding: 0;
  list-style: none;
}

.menu-item {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 9px 12px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: #555;
  font-size: 14px;
  text-align: left;
  cursor: pointer;
}

.menu-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.menu-caret {
  color: #999;
  font-size: 11px;
}

.submenu-list {
  margin: 2px 0 0;
  padding: 0 0 0 12px;
  list-style: none;
}

.menu-item.submenu {
  padding: 7px 12px;
  font-size: 13px;
}

.menu-item:hover {
  background: #ececec;
  color: #333;
}

.menu-item.active {
  background: #e3f2e6;
  color: #1a7f37;
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
  max-width: 90px;
  overflow: hidden;
  color: #666;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.link-btn {
  padding: 4px 8px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: #d33;
  font-size: 12px;
  cursor: pointer;
}

.link-btn:hover {
  background: #fdecec;
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

.back-btn {
  padding: 7px 14px;
  border: 1px solid #1a7f37;
  border-radius: 8px;
  background: #fff;
  color: #1a7f37;
  font-size: 13px;
  cursor: pointer;
}

.back-btn:hover {
  background: #e3f2e6;
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

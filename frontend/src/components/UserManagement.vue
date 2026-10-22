<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

import {
  createUser,
  deleteUser,
  fetchUsers,
  updateUser,
  type AdminUser,
  type AdminUserUpdatePayload,
} from '@/api/admin'
import { getUsername } from '@/api/auth'

/** 新建/编辑共用的表单态；id 为 null 表示新建。 */
interface UserForm {
  id: number | null
  username: string
  password: string
  is_admin: boolean
}

const users = ref<AdminUser[]>([])
const loading = ref(false)
const saving = ref(false)
const deletingId = ref<number | null>(null)
const errorMsg = ref('')
const form = ref<UserForm | null>(null)
const pendingDelete = ref<AdminUser | null>(null)
const currentUsername = getUsername() ?? ''

function errText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

function formatTime(value: string): string {
  // 后端返回 "2026-10-21T18:56:00"，展示到分钟即可
  return value.replace('T', ' ').slice(0, 16)
}

function isSelf(user: AdminUser): boolean {
  return user.username === currentUsername
}

async function loadUsers() {
  loading.value = true
  errorMsg.value = ''
  try {
    users.value = await fetchUsers()
  } catch (err) {
    errorMsg.value = errText(err)
  } finally {
    loading.value = false
  }
}

function openCreate() {
  errorMsg.value = ''
  form.value = { id: null, username: '', password: '', is_admin: false }
}

function openEdit(user: AdminUser) {
  errorMsg.value = ''
  // 编辑时密码留空表示不修改
  form.value = { id: user.id, username: user.username, password: '', is_admin: user.is_admin }
}

function closeForm() {
  if (saving.value) return
  form.value = null
}

async function submitForm() {
  const draft = form.value
  if (!draft || saving.value) return
  const name = draft.username.trim()
  if (!name) {
    errorMsg.value = '用户名不能为空'
    return
  }
  if (draft.id === null && draft.password.length < 4) {
    errorMsg.value = '新建用户的密码至少 4 位'
    return
  }
  if (draft.id !== null && draft.password.length > 0 && draft.password.length < 4) {
    errorMsg.value = '新密码至少 4 位'
    return
  }

  saving.value = true
  errorMsg.value = ''
  try {
    if (draft.id === null) {
      await createUser({ username: name, password: draft.password, is_admin: draft.is_admin })
    } else {
      const payload: AdminUserUpdatePayload = { username: name, is_admin: draft.is_admin }
      if (draft.password) payload.password = draft.password
      await updateUser(draft.id, payload)
    }
    form.value = null
    await loadUsers()
  } catch (err) {
    errorMsg.value = errText(err)
  } finally {
    saving.value = false
  }
}

function askDelete(user: AdminUser) {
  if (saving.value || deletingId.value !== null) return
  errorMsg.value = ''
  pendingDelete.value = user
}

function cancelDelete() {
  if (deletingId.value !== null) return
  pendingDelete.value = null
}

async function confirmDelete() {
  const user = pendingDelete.value
  if (!user || deletingId.value !== null) return
  deletingId.value = user.id
  errorMsg.value = ''
  try {
    await deleteUser(user.id)
    users.value = users.value.filter((item) => item.id !== user.id)
    pendingDelete.value = null
  } catch (err) {
    errorMsg.value = errText(err)
    pendingDelete.value = null
  } finally {
    deletingId.value = null
  }
}

/** Esc 关闭弹框：焦点往往在 body 上，所以监听 document 而不是弹框容器。 */
function onKeydown(event: KeyboardEvent) {
  if (event.key !== 'Escape') return
  if (pendingDelete.value) cancelDelete()
  else if (form.value) closeForm()
}

onMounted(() => {
  loadUsers()
  document.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <section class="panel">
    <div class="panel-head">
      <div>
        <h3 class="panel-title">用户管理</h3>
        <p class="panel-desc">查看系统所有用户，可新增、修改或删除账号。</p>
      </div>
      <div class="head-actions">
        <el-button :disabled="loading" @click="loadUsers">刷新</el-button>
        <el-button type="primary" :disabled="loading" @click="openCreate">新建用户</el-button>
      </div>
    </div>

    <el-alert
      v-if="errorMsg"
      :title="errorMsg"
      type="error"
      show-icon
      :closable="false"
      class="panel-error"
    />

    <el-table
      v-loading="loading"
      :data="users"
      empty-text="暂无用户"
      style="width: 100%"
    >
      <el-table-column prop="id" label="ID" width="80" />
      <el-table-column label="用户名" min-width="160">
        <template #default="{ row }">
          <span>{{ row.username }}</span>
          <span v-if="isSelf(row)" class="self-tag">当前账号</span>
        </template>
      </el-table-column>
      <el-table-column label="角色" width="120">
        <template #default="{ row }">
          <el-tag :type="row.is_admin ? 'success' : 'info'" size="small">
            {{ row.is_admin ? '管理员' : '普通用户' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="创建时间" min-width="150">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="130">
        <template #default="{ row }">
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button
            link
            type="danger"
            :disabled="isSelf(row)"
            :title="isSelf(row) ? '不能删除当前登录的账号' : '删除用户'"
            @click="askDelete(row)"
          >
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新建 / 编辑用户 -->
    <el-dialog
      :model-value="form !== null"
      :title="form ? (form.id === null ? '新建用户' : '编辑用户') : ''"
      width="360px"
      @close="closeForm"
    >
      <el-form v-if="form" label-position="top" @submit.prevent="submitForm">
        <el-form-item label="用户名">
          <el-input v-model="form.username" maxlength="32" placeholder="2-32 个字符" />
        </el-form-item>
        <el-form-item :label="form.id === null ? '密码' : '新密码'">
          <el-input
            v-model="form.password"
            type="password"
            maxlength="64"
            show-password
            :placeholder="form.id === null ? '至少 4 位' : '留空表示不修改'"
          />
        </el-form-item>
        <el-form-item>
          <el-switch v-model="form.is_admin" />
          <span class="switch-label">设为管理员</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="saving" @click="closeForm">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitForm">保存</el-button>
      </template>
    </el-dialog>

    <!-- 删除确认 -->
    <el-dialog
      :model-value="pendingDelete !== null"
      title="删除用户"
      width="360px"
      @close="cancelDelete"
    >
      <p v-if="pendingDelete" class="dialog-text">
        确定删除用户「{{ pendingDelete.username }}」吗？其会话将一并标记删除，数据保留在库内。
      </p>
      <template #footer>
        <el-button :disabled="deletingId !== null" @click="cancelDelete">取消</el-button>
        <el-button
          type="danger"
          :loading="deletingId !== null && deletingId === pendingDelete?.id"
          :disabled="deletingId !== null"
          @click="confirmDelete"
        >
          删除
        </el-button>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.panel {
  padding: 18px 20px;
  border: 1px solid #e5e5e5;
  border-radius: 10px;
  background: #fff;
}

.panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.panel-title {
  margin: 0 0 6px;
  color: #333;
  font-size: 15px;
  font-weight: 600;
}

.panel-desc {
  margin: 0;
  color: #888;
  font-size: 13px;
}

.head-actions {
  display: flex;
  gap: 8px;
}

.panel-error {
  margin-bottom: 12px;
}

.self-tag {
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: 10px;
  background: #eef2f5;
  color: #667;
  font-size: 11px;
}

.switch-label {
  margin-left: 10px;
  color: #555;
  font-size: 13px;
}

.dialog-text {
  margin: 0;
  color: #666;
  font-size: 13px;
  line-height: 1.6;
  word-break: break-all;
}
</style>

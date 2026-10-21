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
        <button class="ghost-btn" :disabled="loading" @click="loadUsers">刷新</button>
        <button class="primary-btn" :disabled="loading" @click="openCreate">新建用户</button>
      </div>
    </div>

    <p v-if="errorMsg" class="panel-error">{{ errorMsg }}</p>

    <div class="table-wrap">
      <table class="user-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>用户名</th>
            <th>角色</th>
            <th>创建时间</th>
            <th class="col-action">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td class="table-tip" colspan="5">加载中…</td>
          </tr>
          <tr v-else-if="users.length === 0">
            <td class="table-tip" colspan="5">暂无用户</td>
          </tr>
          <tr v-for="user in users" v-else :key="user.id">
            <td>{{ user.id }}</td>
            <td>
              {{ user.username }}
              <span v-if="isSelf(user)" class="self-tag">当前账号</span>
            </td>
            <td>
              <span class="role-tag" :class="{ admin: user.is_admin }">
                {{ user.is_admin ? '管理员' : '普通用户' }}
              </span>
            </td>
            <td>{{ formatTime(user.created_at) }}</td>
            <td class="col-action">
              <button class="link-btn edit" @click="openEdit(user)">编辑</button>
              <button
                class="link-btn remove"
                :disabled="isSelf(user)"
                :title="isSelf(user) ? '不能删除当前登录的账号' : '删除用户'"
                @click="askDelete(user)"
              >
                删除
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 新建 / 编辑用户 -->
    <Teleport to="body">
      <div v-if="form" class="dialog-mask" @click.self="closeForm">
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="user-dialog-title">
          <h3 id="user-dialog-title" class="dialog-title">
            {{ form.id === null ? '新建用户' : '编辑用户' }}
          </h3>
          <label class="field">
            <span>用户名</span>
            <input v-model="form.username" type="text" maxlength="32" placeholder="2-32 个字符" />
          </label>
          <label class="field">
            <span>{{ form.id === null ? '密码' : '新密码' }}</span>
            <input
              v-model="form.password"
              type="password"
              maxlength="64"
              :placeholder="form.id === null ? '至少 4 位' : '留空表示不修改'"
            />
          </label>
          <label class="field checkbox">
            <input v-model="form.is_admin" type="checkbox" />
            <span>设为管理员</span>
          </label>
          <div class="dialog-actions">
            <button class="dialog-btn ghost" :disabled="saving" @click="closeForm">取消</button>
            <button class="dialog-btn primary" :disabled="saving" @click="submitForm">
              {{ saving ? '保存中…' : '保存' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 删除确认 -->
    <Teleport to="body">
      <div v-if="pendingDelete" class="dialog-mask" @click.self="cancelDelete">
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="delete-user-title">
          <h3 id="delete-user-title" class="dialog-title">删除用户</h3>
          <p class="dialog-text">
            确定删除用户「{{ pendingDelete.username }}」吗？其会话将一并标记删除，数据保留在库内。
          </p>
          <div class="dialog-actions">
            <button class="dialog-btn ghost" :disabled="deletingId !== null" @click="cancelDelete">
              取消
            </button>
            <button
              class="dialog-btn danger"
              :disabled="deletingId !== null"
              @click="confirmDelete"
            >
              {{ deletingId === pendingDelete.id ? '删除中…' : '删除' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
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

.primary-btn,
.ghost-btn {
  padding: 7px 14px;
  border-radius: 8px;
  font-size: 13px;
  cursor: pointer;
}

.primary-btn {
  border: 1px solid #1a7f37;
  background: #1a7f37;
  color: #fff;
}

.primary-btn:hover:not(:disabled) {
  background: #166b30;
}

.ghost-btn {
  border: 1px solid #d9d9d9;
  background: #fff;
  color: #555;
}

.ghost-btn:hover:not(:disabled) {
  border-color: #1a7f37;
  color: #1a7f37;
}

.primary-btn:disabled,
.ghost-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.panel-error {
  margin: 0 0 12px;
  padding: 8px 10px;
  border: 1px solid #f5c2c7;
  border-radius: 8px;
  background: #fdf0f1;
  color: #b02a37;
  font-size: 13px;
}

.table-wrap {
  overflow-x: auto;
}

.user-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.user-table th,
.user-table td {
  padding: 9px 10px;
  border-bottom: 1px solid #eee;
  color: #444;
  text-align: left;
}

.user-table th {
  color: #777;
  font-weight: 600;
  background: #fafafa;
}

.user-table tbody tr:hover {
  background: #f8fbf8;
}

.col-action {
  width: 120px;
}

.table-tip {
  color: #999;
  text-align: center;
}

.self-tag {
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: 10px;
  background: #eef2f5;
  color: #667;
  font-size: 11px;
}

.role-tag {
  padding: 2px 8px;
  border-radius: 10px;
  background: #f0f0f0;
  color: #666;
  font-size: 12px;
}

.role-tag.admin {
  background: #e3f2e6;
  color: #1a7f37;
}

.link-btn {
  padding: 3px 6px;
  border: none;
  border-radius: 6px;
  background: transparent;
  font-size: 12px;
  cursor: pointer;
}

.link-btn.edit {
  color: #1a7f37;
}

.link-btn.edit:hover {
  background: #e3f2e6;
}

.link-btn.remove {
  color: #d33;
}

.link-btn.remove:hover:not(:disabled) {
  background: #fdecec;
}

.link-btn:disabled {
  color: #bbb;
  cursor: not-allowed;
}

/* ---------- 弹框 ---------- */
.dialog-mask {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(20, 22, 26, 0.4);
  z-index: 30;
}

.dialog {
  width: calc(100% - 40px);
  max-width: 360px;
  padding: 18px 20px;
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.18);
  box-sizing: border-box;
}

.dialog-title {
  margin: 0 0 10px;
  color: #333;
  font-size: 16px;
  font-weight: 600;
}

.dialog-text {
  margin: 0;
  color: #666;
  font-size: 13px;
  line-height: 1.6;
  word-break: break-all;
}

.field {
  display: block;
  margin-bottom: 12px;
}

.field span {
  display: block;
  margin-bottom: 5px;
  color: #666;
  font-size: 13px;
}

.field input[type='text'],
.field input[type='password'] {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid #d9d9d9;
  border-radius: 8px;
  font-size: 13px;
  box-sizing: border-box;
  outline: none;
}

.field input[type='text']:focus,
.field input[type='password']:focus {
  border-color: #1a7f37;
}

.field.checkbox {
  display: flex;
  align-items: center;
  gap: 6px;
}

.field.checkbox span {
  margin: 0;
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}

.dialog-btn {
  min-width: 68px;
  padding: 7px 14px;
  border-radius: 8px;
  font-size: 13px;
  cursor: pointer;
}

.dialog-btn.ghost {
  border: 1px solid #d9d9d9;
  background: #fff;
  color: #555;
}

.dialog-btn.ghost:hover {
  background: #f2f2f2;
}

.dialog-btn.primary {
  border: 1px solid #1a7f37;
  background: #1a7f37;
  color: #fff;
}

.dialog-btn.primary:hover:not(:disabled) {
  background: #166b30;
}

.dialog-btn.danger {
  border: 1px solid #d33;
  background: #d33;
  color: #fff;
}

.dialog-btn.danger:hover:not(:disabled) {
  background: #b32b2b;
}

.dialog-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>

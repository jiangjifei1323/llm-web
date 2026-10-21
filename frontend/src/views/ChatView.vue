<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { clearAuth, getUsername } from '@/api/auth'
import {
  fetchConversationMessages,
  fetchConversations,
  renameConversation,
  streamChat,
  type ChatMessage,
  type ConversationSummary,
} from '@/api/chat'

const router = useRouter()
const username = ref(getUsername() ?? '')
const avatarText = computed(() =>
  username.value ? username.value.slice(0, 1).toUpperCase() : '?',
)

const messages = ref<ChatMessage[]>([])
const input = ref('')
const loading = ref(false)
const errorMsg = ref('')
const listRef = ref<HTMLElement | null>(null)
const userMenuOpen = ref(false)
const userAreaRef = ref<HTMLElement | null>(null)

// 左侧会话列表
const conversations = ref<ConversationSummary[]>([])
const historyLoading = ref(false)

// 会话 id：持久化在 sessionStorage，刷新页面仍可延续同一会话
const CONVERSATION_KEY = 'llm_conversation_id'
const conversationId = ref<string | null>(sessionStorage.getItem(CONVERSATION_KEY))

function errText(err: unknown): string {
  return err instanceof Error ? err.message : '未知错误'
}

function resetConversation() {
  messages.value = []
  errorMsg.value = ''
  conversationId.value = null
  sessionStorage.removeItem(CONVERSATION_KEY)
}

function logout() {
  userMenuOpen.value = false
  clearAuth()
  sessionStorage.removeItem(CONVERSATION_KEY)
  void router.replace({ name: 'login' })
}

async function scrollToBottom() {
  await nextTick()
  listRef.value?.scrollTo({ top: listRef.value.scrollHeight })
}

async function loadConversations() {
  try {
    conversations.value = await fetchConversations()
  } catch (err) {
    errorMsg.value = `会话列表加载失败：${errText(err)}`
  }
}

/** 拉取指定会话的历史消息并切换为当前会话。 */
async function loadHistory(id: string) {
  errorMsg.value = ''
  historyLoading.value = true
  try {
    messages.value = await fetchConversationMessages(id)
    conversationId.value = id
    sessionStorage.setItem(CONVERSATION_KEY, id)
    await scrollToBottom()
  } catch (err) {
    errorMsg.value = `历史消息加载失败：${errText(err)}`
  } finally {
    historyLoading.value = false
  }
}

function openConversation(id: string) {
  if (loading.value || historyLoading.value || id === conversationId.value) return
  void loadHistory(id)
}

// ---------- 会话改名 ----------
const editingId = ref('')
const editingTitle = ref('')
const renameSaving = ref(false)
// 同时只有一个编辑框，用普通变量持有它以便聚焦
let editInputEl: HTMLInputElement | null = null

function setEditInput(el: unknown) {
  editInputEl = el instanceof HTMLInputElement ? el : null
}

async function startRename(conv: ConversationSummary) {
  editingId.value = conv.id
  editingTitle.value = conv.title
  await nextTick()
  editInputEl?.focus()
  editInputEl?.select()
}

function cancelRename() {
  editingId.value = ''
  editingTitle.value = ''
}

async function saveRename() {
  const id = editingId.value
  const title = editingTitle.value.trim()
  if (!id || !title || renameSaving.value) return
  renameSaving.value = true
  errorMsg.value = ''
  try {
    const saved = await renameConversation(id, title)
    const conv = conversations.value.find((item) => item.id === id)
    if (conv) conv.title = saved
    cancelRename()
  } catch (err) {
    errorMsg.value = `重命名失败：${errText(err)}`
  } finally {
    renameSaving.value = false
  }
}

function formatTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

// 点击用户入口外部区域时收起弹框
function onDocumentClick(e: MouseEvent) {
  if (!userMenuOpen.value) return
  if (userAreaRef.value?.contains(e.target as Node)) return
  userMenuOpen.value = false
}

onMounted(async () => {
  document.addEventListener('click', onDocumentClick)
  await loadConversations()
  // 刷新前正在进行的会话，重新拉取历史以还原界面
  if (conversationId.value) await loadHistory(conversationId.value)
})

onUnmounted(() => {
  document.removeEventListener('click', onDocumentClick)
})

async function submit() {
  const question = input.value.trim()
  if (!question || loading.value) return

  errorMsg.value = ''
  messages.value.push({ role: 'user', content: question })
  input.value = ''
  loading.value = true
  await scrollToBottom()

  // 先插入一条空的助手消息，流式增量逐块填充进去
  const replyIndex = messages.value.length
  messages.value.push({ role: 'assistant', content: '' })

  try {
    // 除当前问题与占位回复外的历史一并发给后端，让模型拥有上下文
    const history = messages.value.slice(0, -2)
    await streamChat(question, history, {
      conversationId: conversationId.value,
      onMeta: (meta) => {
        if (meta.conversationId) {
          conversationId.value = meta.conversationId
          sessionStorage.setItem(CONVERSATION_KEY, meta.conversationId)
        }
      },
      onDelta: (delta) => {
        const reply = messages.value[replyIndex]
        if (reply) reply.content += delta
        void scrollToBottom()
      },
    })
  } catch (err) {
    errorMsg.value = errText(err)
    const reply = messages.value[replyIndex]
    if (reply && !reply.content) {
      // 尚未收到任何内容，移除空占位并把问题还给输入框
      messages.value.splice(replyIndex, 1)
      input.value = question
    }
  } finally {
    loading.value = false
    await scrollToBottom()
    // 本轮问答已落库，刷新列表以出现新会话或更新标题时间
    if (conversationId.value) void loadConversations()
  }
}

function onKeydown(e: KeyboardEvent) {
  // Enter 发送，Shift+Enter 换行
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    void submit()
  }
}
</script>

<template>
  <div class="chat-layout">
    <aside class="sidebar">
      <h2 class="sidebar-title">会话列表</h2>

      <p v-if="conversations.length === 0" class="conv-empty">暂无历史会话</p>
      <ul v-else class="conv-list">
        <li v-for="conv in conversations" :key="conv.id" class="conv-row">
          <form
            v-if="editingId === conv.id"
            class="conv-edit-form"
            @submit.prevent="saveRename"
          >
            <input
              v-model="editingTitle"
              :ref="setEditInput"
              class="conv-edit"
              maxlength="200"
              aria-label="会话名称"
              @keydown.esc="cancelRename"
            />
            <div class="edit-actions">
              <button type="submit" class="edit-save" :disabled="renameSaving || !editingTitle.trim()">
                保存
              </button>
              <button type="button" class="edit-cancel" :disabled="renameSaving" @click="cancelRename">
                取消
              </button>
            </div>
          </form>
          <template v-else>
            <button
              class="conv-item"
              :class="{ active: conv.id === conversationId }"
              :disabled="loading || historyLoading"
              @click="openConversation(conv.id)"
            >
              <span class="conv-title">{{ conv.title }}</span>
              <span class="conv-meta">
                {{ conv.rounds }} 轮 · {{ formatTime(conv.updated_at) }}
              </span>
            </button>
            <button
              class="rename-btn"
              title="重命名"
              aria-label="重命名会话"
              :disabled="loading || historyLoading"
              @click="startRename(conv)"
            >
              ✎
            </button>
          </template>
        </li>
      </ul>

      <div ref="userAreaRef" class="user-area">
        <button class="user-chip" :aria-expanded="userMenuOpen" @click="userMenuOpen = !userMenuOpen">
          <span class="avatar">{{ avatarText }}</span>
          <span class="chip-name">{{ username }}</span>
        </button>

        <div v-if="userMenuOpen" class="user-popover">
          <p class="pop-username">{{ username }}</p>
          <button class="logout-btn" :disabled="loading" @click="logout">退出登录</button>
        </div>
      </div>
    </aside>

    <div class="chat-page">
      <header class="chat-header">
        <h1>问答助手</h1>
        <p class="subtitle">基于 Vue + FastAPI 的基础问答</p>
        <div class="header-actions">
          <button class="reset-btn" :disabled="loading" @click="resetConversation">新建会话</button>
        </div>
      </header>

      <div ref="listRef" class="message-list">
        <p v-if="historyLoading" class="empty-tip">历史消息加载中…</p>
        <p v-else-if="messages.length === 0" class="empty-tip">输入问题开始对话</p>
        <div
          v-for="(msg, index) in messages"
          :key="index"
          class="message-row"
          :class="msg.role"
        >
          <div class="bubble" :class="{ thinking: msg.role === 'assistant' && !msg.content }">
            {{ msg.content || (loading && index === messages.length - 1 ? '思考中…' : '') }}
          </div>
        </div>
      </div>

      <p v-if="errorMsg" class="error">{{ errorMsg }}</p>

      <footer class="chat-input-bar">
        <textarea
          v-model="input"
          class="chat-input"
          placeholder="请输入问题，Enter 发送，Shift+Enter 换行"
          rows="3"
          @keydown="onKeydown"
        ></textarea>
        <button class="send-btn" :disabled="loading || !input.trim()" @click="submit">
          {{ loading ? '回答中…' : '发送' }}
        </button>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.chat-layout {
  display: flex;
  height: 100vh;
}

/* ---------- 左侧会话栏 ---------- */
.sidebar {
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  width: 240px;
  padding: 14px 12px 12px;
  border-right: 1px solid #e5e5e5;
  background: #f7f8fa;
  box-sizing: border-box;
}

.sidebar-title {
  margin: 0 0 10px;
  font-size: 14px;
  color: #666;
}

.conv-empty {
  flex: 1;
  margin-top: 24px;
  text-align: center;
  color: #aaa;
  font-size: 13px;
}

.conv-list {
  flex: 1;
  margin: 0;
  padding: 0;
  list-style: none;
  overflow-y: auto;
}

.conv-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
  width: 100%;
  margin-bottom: 4px;
  padding: 8px 30px 8px 10px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.conv-item:hover:not(:disabled) {
  background: #eceef1;
}

.conv-item.active {
  border-color: #bcdcc6;
  background: #e6f2ea;
}

.conv-item:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.conv-title {
  overflow: hidden;
  color: #333;
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conv-meta {
  color: #999;
  font-size: 12px;
}

/* 会话行：为右上的重命名按钮定位 */
.conv-row {
  position: relative;
}

.rename-btn {
  position: absolute;
  top: 7px;
  right: 6px;
  width: 22px;
  height: 22px;
  padding: 0;
  border: none;
  border-radius: 5px;
  background: transparent;
  color: #777;
  font-size: 13px;
  line-height: 1;
  opacity: 0.45;
  cursor: pointer;
}

.rename-btn:hover:not(:disabled),
.rename-btn:focus-visible {
  background: #dfe3e8;
  color: #1a7f37;
  opacity: 1;
}

.rename-btn:disabled {
  cursor: not-allowed;
  opacity: 0.2;
}

/* 就地改名表单 */
.conv-edit-form {
  margin-bottom: 4px;
  padding: 6px 8px 8px;
  border: 1px solid #bcdcc6;
  border-radius: 8px;
  background: #fff;
}

.conv-edit {
  width: 100%;
  padding: 4px 6px;
  border: 1px solid #d0d0d0;
  border-radius: 6px;
  font-size: 13px;
  font-family: inherit;
  box-sizing: border-box;
}

.conv-edit:focus {
  outline: none;
  border-color: #1a7f37;
}

.edit-actions {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
  margin-top: 6px;
}

.edit-save,
.edit-cancel {
  padding: 3px 10px;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
}

.edit-save {
  border: none;
  background: #1a7f37;
  color: #fff;
}

.edit-cancel {
  border: 1px solid #d0d0d0;
  background: #fff;
  color: #555;
}

.edit-save:disabled,
.edit-cancel:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ---------- 右侧主区 ---------- */
.chat-page {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  height: 100vh;
  max-width: 760px;
  margin: 0 auto;
  padding: 16px;
  box-sizing: border-box;
}

.chat-header {
  text-align: center;
  padding: 8px 0 12px;
}

.chat-header h1 {
  margin: 0;
  font-size: 22px;
}

.subtitle {
  margin: 4px 0 0;
  color: #888;
  font-size: 13px;
}

.header-actions {
  margin-top: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
}

.reset-btn {
  padding: 4px 12px;
  border: 1px solid #d0d0d0;
  border-radius: 6px;
  background: #fff;
  color: #555;
  font-size: 12px;
  cursor: pointer;
}

.reset-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px 4px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.empty-tip {
  text-align: center;
  color: #aaa;
  margin-top: 40%;
}

.message-row {
  display: flex;
}

.message-row.user {
  justify-content: flex-end;
}

.message-row.assistant {
  justify-content: flex-start;
}

.bubble {
  max-width: 80%;
  padding: 10px 14px;
  border-radius: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.6;
  font-size: 15px;
}

.message-row.user .bubble {
  background: #1a7f37;
  color: #fff;
  border-bottom-right-radius: 4px;
}

.message-row.assistant .bubble {
  background: #f2f3f5;
  color: #333;
  border-bottom-left-radius: 4px;
}

.bubble.thinking {
  color: #999;
}

.error {
  margin: 4px 0;
  color: #d33;
  font-size: 13px;
  text-align: center;
}

.chat-input-bar {
  display: flex;
  gap: 10px;
  align-items: flex-end;
  padding-top: 8px;
  border-top: 1px solid #e5e5e5;
}

/* 左下角用户入口 */
.user-area {
  position: relative;
  flex-shrink: 0;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid #e5e5e5;
}

.user-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border: 1px solid #d0d0d0;
  border-radius: 18px;
  background: #fff;
  color: #555;
  font-size: 13px;
  cursor: pointer;
}

.user-chip:hover {
  border-color: #1a7f37;
  color: #1a7f37;
}

.avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #1a7f37;
  color: #fff;
  font-size: 12px;
}

.chip-name {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-popover {
  position: absolute;
  bottom: 100%;
  left: 0;
  min-width: 160px;
  margin-bottom: 8px;
  padding: 10px;
  border: 1px solid #e5e5e5;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.12);
}

.pop-username {
  margin: 0 0 8px;
  color: #333;
  font-size: 14px;
  font-weight: 600;
}

.logout-btn {
  width: 100%;
  padding: 7px 0;
  border: 1px solid #f0cfcf;
  border-radius: 6px;
  background: #fff;
  color: #d33;
  font-size: 13px;
  cursor: pointer;
}

.logout-btn:hover {
  background: #fdecec;
}

.logout-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.chat-input {
  flex: 1;
  resize: none;
  padding: 10px 12px;
  border: 1px solid #d0d0d0;
  border-radius: 8px;
  font-size: 15px;
  font-family: inherit;
  box-sizing: border-box;
}

.chat-input:focus {
  outline: none;
  border-color: #1a7f37;
}

.send-btn {
  padding: 10px 22px;
  border: none;
  border-radius: 8px;
  background: #1a7f37;
  color: #fff;
  font-size: 15px;
  cursor: pointer;
}

.send-btn:disabled {
  background: #b5cdbd;
  cursor: not-allowed;
}
</style>

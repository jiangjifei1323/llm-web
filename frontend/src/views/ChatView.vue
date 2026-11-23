<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CopyDocument, SortUp, SortDown } from '@element-plus/icons-vue'

import { clearAuth, fetchMe, getUsername, isAdmin } from '@/api/auth'
import {
  cancelAgentTask,
  createAgentTask,
  resolveAgentApproval,
  subscribeAgentEvents,
  type AgentMode,
} from '@/api/agent'
import {
  deleteConversation,
  fetchConversationMessages,
  fetchConversations,
  renameConversation,
  streamChat,
  submitFeedback,
  type ChatMessage,
  type ConversationSummary,
} from '@/api/chat'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import { fetchKnowledgeBases, MAX_KB_PER_CHAT, type KnowledgeBase } from '@/api/kb'

const router = useRouter()
const username = ref(getUsername() ?? '')
const adminUser = ref(isAdmin())
const avatarText = computed(() =>
  username.value ? username.value.slice(0, 1).toUpperCase() : '?',
)

const messages = ref<ChatMessage[]>([])
const input = ref('')
const loading = ref(false)
const errorMsg = ref('')
const listRef = ref<HTMLElement | null>(null)
// 用户入口弹框显隐，交由 el-popover 的 v-model:visible 管理
const userMenuOpen = ref(false)

// AbortController 用于中断流式请求 / 事件订阅
const abortController = ref<AbortController | null>(null)
// 当前 Agent 任务（停止时需调用取消接口）
const activeTaskId = ref<string | null>(null)
// auto=后端分流；agent=强制任务；chat=强制普通问答
const chatMode = ref<AgentMode>('auto')
const agentTrace = ref<string[]>([])

// 左侧会话列表
const conversations = ref<ConversationSummary[]>([])
const historyLoading = ref(false)

// 会话搜索
const searchKeyword = ref('')
let searchDebounceTimer: ReturnType<typeof setTimeout> | null = null

// 会话 id：持久化在 sessionStorage，刷新页面仍可延续同一会话
const CONVERSATION_KEY = 'llm_conversation_id'
const conversationId = ref<string | null>(sessionStorage.getItem(CONVERSATION_KEY))

// ---------- 知识库（RAG） ----------
// 选中的库持久化在 localStorage：换会话、刷新后仍沿用同一组检索范围
const KB_STORAGE_KEY = 'llm_kb_ids'
const kbs = ref<KnowledgeBase[]>([])
const kbIds = ref<number[]>(readStoredKbIds())

function readStoredKbIds(): number[] {
  try {
    const raw = JSON.parse(localStorage.getItem(KB_STORAGE_KEY) ?? '[]')
    return Array.isArray(raw) ? raw.filter((id): id is number => typeof id === 'number') : []
  } catch {
    return []
  }
}

function storeKbIds(ids: number[]) {
  localStorage.setItem(KB_STORAGE_KEY, JSON.stringify(ids))
}

/** 拉取可选知识库；失败不影响问答本身，只是没有库可选。 */
async function loadKbs() {
  try {
    kbs.value = await fetchKnowledgeBases()
    const valid = new Set(kbs.value.map((kb) => kb.id))
    // 已被删除的库从选中项里剔除，避免请求携带非法 kb_ids 被后端拒绝
    const kept = kbIds.value.filter((id) => valid.has(id))
    if (kept.length !== kbIds.value.length) {
      kbIds.value = kept
      storeKbIds(kept)
    }
  } catch {
    /* 知识库列表加载失败时保持原样，问答可正常进行 */
  }
}

function onKbChange(ids: number[]) {
  storeKbIds(ids)
}

/** 参考资料正文较长，列表中先截断展示。 */
function excerpt(text: string, max = 160): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  return flat.length > max ? `${flat.slice(0, max)}…` : flat
}

function errText(err: unknown): string {
  return err instanceof Error ? err.message : '未知错误'
}

function resetConversation() {
  messages.value = []
  errorMsg.value = ''
  conversationId.value = null
  sessionStorage.removeItem(CONVERSATION_KEY)
}

/** 高风险工具人工审批弹窗 */
async function promptApproval(taskId: string, data: Record<string, unknown>) {
  if (!taskId) return
  const tool = String(data.tool || '')
  const risk = String(data.risk || '')
  const argsText = JSON.stringify(data.args ?? {}, null, 2)
  try {
    await ElMessageBox.confirm(
      `工具：${tool}\n风险：${risk}\n参数：\n${argsText}`,
      '需要人工审批',
      {
        distinguishCancelAndClose: true,
        confirmButtonText: '批准执行',
        cancelButtonText: '拒绝',
        type: 'warning',
        customClass: 'agent-approval-box',
      },
    )
    await resolveAgentApproval(taskId, { action: 'approve' })
    ElMessage.success('已批准，任务继续执行')
  } catch (action) {
    if (action === 'cancel') {
      try {
        await resolveAgentApproval(taskId, { action: 'reject', reason: '用户拒绝' })
        ElMessage.info('已拒绝该工具调用')
      } catch (err) {
        errorMsg.value = errText(err)
      }
    }
    // close / 其它：不自动拒绝，用户可稍后处理或取消任务
  }
}

// 停止生成：断开订阅，并请求取消进行中的 Agent 任务
function stopGeneration() {
  const taskId = activeTaskId.value
  if (abortController.value) {
    abortController.value.abort()
    abortController.value = null
  }
  if (taskId) {
    void cancelAgentTask(taskId).catch(() => undefined)
    activeTaskId.value = null
  }
  loading.value = false
}

// 复制消息
async function copyMessage(content: string) {
  try {
    await navigator.clipboard.writeText(content)
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.error('复制失败')
  }
}

// 提交反馈
async function handleFeedback(msg: ChatMessage, rating: number) {
  if (!msg.id) return
  try {
    await submitFeedback(msg.id, rating)
    msg.rating = rating
    ElMessage.success(rating === 2 ? '感谢点赞' : '已收到反馈')
  } catch {
    ElMessage.error('反馈提交失败')
  }
}

function logout() {
  userMenuOpen.value = false
  clearAuth()
  sessionStorage.removeItem(CONVERSATION_KEY)
  void router.replace({ name: 'login' })
}

/** 进入后台管理页（入口只对管理员展示，路由守卫还会再校验一次权限）。 */
function goAdmin() {
  userMenuOpen.value = false
  void router.push({ name: 'admin' })
}

async function scrollToBottom() {
  await nextTick()
  listRef.value?.scrollTo({ top: listRef.value.scrollHeight })
}

async function loadConversations(keyword?: string) {
  try {
    conversations.value = await fetchConversations(keyword)
  } catch (err) {
    errorMsg.value = `会话列表加载失败：${errText(err)}`
  }
}

// 搜索防抖
function onSearchInput() {
  if (searchDebounceTimer) clearTimeout(searchDebounceTimer)
  searchDebounceTimer = setTimeout(() => {
    void loadConversations(searchKeyword.value)
  }, 300)
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

// ---------- 会话删除 ----------
const deletingId = ref('')
const pendingDelete = ref<ConversationSummary | null>(null)

/** 打开 el-dialog 确认弹框（替代样式不可控的原生 confirm）。 */
function askDelete(conv: ConversationSummary) {
  if (loading.value || historyLoading.value || deletingId.value) return
  errorMsg.value = ''
  pendingDelete.value = conv
}

function cancelDelete() {
  if (deletingId.value) return
  pendingDelete.value = null
}

/** 确认后执行软删除：本地从列表移除，删的是当前打开的会话时重置为新会话。 */
async function confirmDelete() {
  const conv = pendingDelete.value
  if (!conv || deletingId.value) return

  deletingId.value = conv.id
  errorMsg.value = ''
  try {
    await deleteConversation(conv.id)
    conversations.value = conversations.value.filter((item) => item.id !== conv.id)
    if (conversationId.value === conv.id) resetConversation()
    pendingDelete.value = null
  } catch (err) {
    errorMsg.value = `删除失败：${errText(err)}`
    pendingDelete.value = null
  } finally {
    deletingId.value = ''
  }
}

function formatTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

onMounted(async () => {
  // 向后端确认真实身份与管理员标识，纠正登录态里可能过期的本地标记
  try {
    const me = await fetchMe()
    username.value = me.username
    adminUser.value = me.is_admin
  } catch {
    /* 令牌失效时 fetchMe 已跳转登录页，这里不阻断会话列表加载 */
  }
  await loadConversations()
  void loadKbs()
  // 刷新前正在进行的会话，重新拉取历史以还原界面
  if (conversationId.value) await loadHistory(conversationId.value)
})

async function submit() {
  const question = input.value.trim()
  if (!question || loading.value) return

  errorMsg.value = ''
  agentTrace.value = []
  messages.value.push({ role: 'user', content: question })
  input.value = ''
  loading.value = true

  abortController.value = new AbortController()
  activeTaskId.value = null

  await scrollToBottom()

  const replyIndex = messages.value.length
  messages.value.push({ role: 'assistant', content: '' })

  try {
    const created = await createAgentTask({
      message: question,
      conversation_id: conversationId.value,
      kb_ids: kbIds.value,
      mode: chatMode.value,
    })

    conversationId.value = created.conversation_id
    sessionStorage.setItem(CONVERSATION_KEY, created.conversation_id)

    if (created.mode === 'chat' || !created.task_id) {
      const history = messages.value.slice(0, -2)
      await streamChat(question, history, {
        conversationId: conversationId.value,
        kbIds: kbIds.value,
        signal: abortController.value.signal,
        onMeta: (meta) => {
          if (meta.conversationId) {
            conversationId.value = meta.conversationId
            sessionStorage.setItem(CONVERSATION_KEY, meta.conversationId)
          }
          if (meta.sources && meta.sources.length) {
            const reply = messages.value[replyIndex]
            if (reply) reply.sources = meta.sources
          }
        },
        onDelta: (delta) => {
          const reply = messages.value[replyIndex]
          if (reply) reply.content += delta
          void scrollToBottom()
        },
        onDone: (data) => {
          const reply = messages.value[replyIndex]
          if (reply) {
            if (data.messageId) reply.id = data.messageId
            if (data.inputTokens !== undefined) reply.input_tokens = data.inputTokens
            if (data.outputTokens !== undefined) reply.output_tokens = data.outputTokens
            if (data.totalTokens !== undefined) reply.total_tokens = data.totalTokens
          }
        },
      })
      return
    }

    activeTaskId.value = created.task_id
    await subscribeAgentEvents(created.task_id, {
      signal: abortController.value.signal,
      onEvent: (type, data) => {
        if (type === 'plan.updated') {
          const plan = data.plan
          if (Array.isArray(plan)) {
            const prefix = data.revised ? '重规划' : '计划'
            const reason = typeof data.reason === 'string' && data.reason ? `（${data.reason}）` : ''
            agentTrace.value.push(
              `${prefix}${reason}：` +
                plan.map((s: { title?: string }) => s.title || '').filter(Boolean).join(' → '),
            )
          }
        } else if (type === 'task.resumed') {
          agentTrace.value.push(
            data.reason === 'approval_resolved'
              ? '审批已处理，继续执行'
              : `任务恢复重试（第 ${String(data.retry_count || '')} 次）`,
          )
        } else if (type === 'approval.required') {
          agentTrace.value.push(
            `等待审批：${String(data.tool || '')}（风险 ${String(data.risk || '')}）`,
          )
          void promptApproval(String(activeTaskId.value || ''), data)
        } else if (type === 'approval.resolved') {
          agentTrace.value.push(
            `审批结果：${String(data.action || '')} → ${String(data.tool || '')}`,
          )
        } else if (type === 'step.started') {
          agentTrace.value.push(`开始：${String(data.title || data.step_id || '')}`)
        } else if (type === 'tool.started') {
          agentTrace.value.push(`调用工具：${String(data.tool || '')}`)
        } else if (type === 'tool.finished') {
          agentTrace.value.push(
            `工具完成：${String(data.tool || '')}${data.ok === false ? '（失败）' : ''} — ${String(data.summary || '')}`,
          )
        }
        void scrollToBottom()
      },
      onDelta: (delta) => {
        const reply = messages.value[replyIndex]
        if (reply) reply.content += delta
        void scrollToBottom()
      },
      onCompleted: (data) => {
        const reply = messages.value[replyIndex]
        if (reply) {
          if (data.messageId) reply.id = data.messageId
          if (data.inputTokens !== undefined) reply.input_tokens = data.inputTokens
          if (data.outputTokens !== undefined) reply.output_tokens = data.outputTokens
          if (data.totalTokens !== undefined) reply.total_tokens = data.totalTokens
        }
      },
      onFailed: (error) => {
        errorMsg.value = error
      },
    })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      // 保留已生成的内容
    } else {
      errorMsg.value = errText(err)
      const reply = messages.value[replyIndex]
      if (reply && !reply.content) {
        messages.value.splice(replyIndex, 1)
        input.value = question
      }
    }
  } finally {
    loading.value = false
    abortController.value = null
    activeTaskId.value = null
    await scrollToBottom()
    if (conversationId.value) {
      void loadConversations()
    }
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
      <div class="sidebar-header">
        <h2 class="sidebar-title">会话列表</h2>
        <el-button class="reset-btn" :disabled="loading" @click="resetConversation">
          新建
        </el-button>
      </div>

      <div class="search-box">
        <input
          v-model="searchKeyword"
          type="text"
          class="search-input"
          placeholder="搜索会话..."
          @input="onSearchInput"
        />
      </div>

      <p v-if="conversations.length === 0" class="conv-empty">
        {{ searchKeyword ? '未找到匹配会话' : '暂无历史会话' }}
      </p>
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
              <el-button
                type="primary"
                size="small"
                :disabled="renameSaving || !editingTitle.trim()"
                @click="saveRename"
              >
                保存
              </el-button>
              <el-button size="small" :disabled="renameSaving" @click="cancelRename">
                取消
              </el-button>
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
              <span class="conv-meta">{{ formatTime(conv.updated_at) }}</span>
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
            <button
              class="delete-btn"
              title="删除"
              aria-label="删除会话"
              :disabled="loading || historyLoading || deletingId !== ''"
              @click="askDelete(conv)"
            >
              {{ deletingId === conv.id ? '…' : '✕' }}
            </button>
          </template>
        </li>
      </ul>

      <div class="user-area">
        <el-popover
          v-model:visible="userMenuOpen"
          placement="top-start"
          :width="180"
          trigger="click"
        >
          <template #reference>
            <button class="user-chip">
              <span class="avatar">{{ avatarText }}</span>
              <span class="chip-name">{{ username }}</span>
            </button>
          </template>
          <div class="pop-body">
            <p class="pop-username">{{ username }}</p>
            <el-button v-if="adminUser" type="primary" plain class="pop-btn" @click="goAdmin">
              后台管理
            </el-button>
            <el-button
              type="danger"
              plain
              class="pop-btn"
              :disabled="loading"
              @click="logout"
            >
              退出登录
            </el-button>
          </div>
        </el-popover>
      </div>
    </aside>

    <div class="chat-page">
      <header class="chat-header">
        <h1>问答助手</h1>
        <p class="subtitle">基于 Vue + FastAPI 的基础问答</p>
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
          <div class="bubble" :class="{ thinking: msg.role === 'assistant' && !msg.content && loading && index === messages.length - 1 }">
            <ul
              v-if="msg.role === 'assistant' && index === messages.length - 1 && agentTrace.length"
              class="agent-trace"
            >
              <li v-for="(line, ti) in agentTrace" :key="ti">{{ line }}</li>
            </ul>
            <template v-if="msg.role === 'assistant' && msg.content">
              <MarkdownRenderer :content="msg.content" />
              <div v-if="!loading || index !== messages.length - 1" class="message-actions">
                <button class="action-btn" title="复制" @click="copyMessage(msg.content)">
                  <el-icon><CopyDocument /></el-icon>
                </button>
                <button
                  class="action-btn"
                  :class="{ active: msg.rating === 2 }"
                  title="点赞"
                  @click="handleFeedback(msg, 2)"
                >
                  <el-icon><SortUp /></el-icon>
                </button>
                <button
                  class="action-btn"
                  :class="{ active: msg.rating === 1 }"
                  title="踩"
                  @click="handleFeedback(msg, 1)"
                >
                  <el-icon><SortDown /></el-icon>
                </button>
                <span v-if="msg.total_tokens" class="token-info">
                  {{ msg.total_tokens }} tokens
                </span>
              </div>
            </template>
            <template v-else-if="msg.role !== 'assistant' || !agentTrace.length">
              {{ msg.content || (loading && index === messages.length - 1 ? '思考中…' : '') }}
            </template>

            <!-- 知识库命中的参考资料：文件名 + 相关度 + 正文摘要 -->
            <details v-if="msg.sources && msg.sources.length" class="sources">
              <summary>
                <span class="sources-label">参考资料 {{ msg.sources.length }} 条</span>
                <span class="sources-hint">回答依据的知识库片段</span>
              </summary>
              <ol class="sources-list">
                <li v-for="(src, si) in msg.sources" :key="si" class="source-item">
                  <div class="source-head">
                    <span class="source-index">[{{ si + 1 }}]</span>
                    <span class="source-name" :title="src.filename">{{ src.filename }}</span>
                    <span class="source-score">相关度 {{ Math.round(src.score * 100) }}%</span>
                  </div>
                  <p class="source-content" :title="src.content">{{ excerpt(src.content) }}</p>
                </li>
              </ol>
            </details>
          </div>
        </div>
      </div>

      <p v-if="errorMsg" class="error">{{ errorMsg }}</p>

      <footer class="chat-input-bar">
        <div class="kb-bar">
          <span class="kb-bar-label">模式</span>
          <el-select v-model="chatMode" class="mode-select" size="small">
            <el-option label="自动" value="auto" />
            <el-option label="智能任务" value="agent" />
            <el-option label="普通问答" value="chat" />
          </el-select>
          <!-- 知识库仅管理员可管理，普通用户按 user_id 查不到任何库，不必看到选择条 -->
          <template v-if="adminUser">
            <span class="kb-bar-label">知识库</span>
            <el-select
              v-model="kbIds"
              class="kb-select"
              size="small"
              multiple
              clearable
              collapse-tags
              collapse-tags-tooltip
              :multiple-limit="MAX_KB_PER_CHAT"
              placeholder="不引用（仅模型自身知识作答）"
              @change="onKbChange"
            >
              <el-option v-for="kb in kbs" :key="kb.id" :label="kb.name" :value="kb.id">
                <span class="kb-option-name">{{ kb.name }}</span>
                <span class="kb-option-count">{{ kb.doc_count }} 文档</span>
              </el-option>
              <template v-if="kbs.length === 0" #empty>
                <p class="kb-option-empty">还没有知识库</p>
              </template>
            </el-select>
          </template>
        </div>

        <div class="input-row">
          <textarea
            v-model="input"
            class="chat-input"
            placeholder="请输入问题，Enter 发送，Shift+Enter 换行"
            rows="3"
            @keydown="onKeydown"
          ></textarea>
          <el-button
            v-if="loading"
            type="danger"
            class="send-btn"
            @click="stopGeneration"
          >
            停止
          </el-button>
          <el-button
            v-else
            type="primary"
            class="send-btn"
            :disabled="!input.trim()"
            @click="submit"
          >
            发送
          </el-button>
        </div>
      </footer>
    </div>

    <!-- 会话删除确认 -->
    <el-dialog
      :model-value="pendingDelete !== null"
      title="删除会话"
      width="360px"
      @close="cancelDelete"
    >
      <p v-if="pendingDelete" class="dialog-text">
        确定删除「{{ pendingDelete.title }}」？删除后该会话从列表隐藏，历史消息仍保留在库内。
      </p>
      <template #footer>
        <el-button :disabled="deletingId !== ''" @click="cancelDelete">取消</el-button>
        <el-button
          type="danger"
          :loading="deletingId !== '' && deletingId === pendingDelete?.id"
          :disabled="deletingId !== ''"
          @click="confirmDelete"
        >
          删除
        </el-button>
      </template>
    </el-dialog>
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

.sidebar-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.sidebar-title {
  margin: 0;
  font-size: 14px;
  color: #666;
  flex: 1;
}

/* 新建会话：置于会话列表上方 */
.reset-btn {
  flex-shrink: 0;
}

/* 搜索框 */
.search-box {
  margin-bottom: 10px;
}

.search-input {
  width: 100%;
  padding: 6px 10px;
  border: 1px solid #d0d0d0;
  border-radius: 6px;
  font-size: 13px;
  box-sizing: border-box;
}

.search-input:focus {
  outline: none;
  border-color: #1a7f37;
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
  padding: 8px 52px 8px 10px;
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

/* 会话行：为右上的重命名 / 删除按钮定位 */
.conv-row {
  position: relative;
}

.rename-btn {
  position: absolute;
  top: 7px;
  right: 30px;
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

.delete-btn {
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

.delete-btn:hover:not(:disabled),
.delete-btn:focus-visible {
  background: #fdecec;
  color: #d33;
  opacity: 1;
}

.delete-btn:disabled {
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

.message-row.assistant .bubble :deep(p) {
  margin: 6px 0;
}

.message-row.assistant .bubble :deep(p:first-child) {
  margin-top: 0;
}

.message-row.assistant .bubble :deep(p:last-child) {
  margin-bottom: 0;
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
  flex-direction: column;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid #e5e5e5;
}

.input-row {
  display: flex;
  gap: 10px;
  align-items: flex-end;
}

/* 知识库选择条 */
.kb-bar {
  display: flex;
  align-items: center;
  gap: 6px;
}

.kb-bar-label {
  flex-shrink: 0;
  color: #888;
  font-size: 12px;
}

.mode-select {
  width: 120px;
}

.kb-select {
  width: 300px;
}

.agent-trace {
  margin: 0 0 10px;
  padding: 8px 10px;
  list-style: none;
  border-radius: 8px;
  background: #f5f7fa;
  color: #606266;
  font-size: 12px;
  line-height: 1.55;
}

.agent-trace li + li {
  margin-top: 4px;
}

.kb-option-name {
  float: left;
}

.kb-option-count {
  float: right;
  margin-left: 16px;
  color: #aaa;
  font-size: 12px;
}

.kb-option-empty {
  margin: 0;
  padding: 6px 0;
  color: #aaa;
  font-size: 13px;
  text-align: center;
}

/* 参考资料折叠区 */
.sources {
  margin-top: 8px;
  padding: 6px 8px;
  border: 1px solid #e0e3e6;
  border-radius: 8px;
  background: #fff;
  font-size: 12px;
}

.sources summary {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  color: #1a7f37;
  list-style: none;
}

.sources summary::-webkit-details-marker {
  display: none;
}

.sources-label {
  font-weight: 600;
}

.sources-hint {
  color: #aaa;
  font-size: 11px;
}

.sources-list {
  margin: 6px 0 0;
  padding: 0;
  list-style: none;
}

.source-item {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed #e6e6e6;
}

.source-head {
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.source-index {
  color: #1a7f37;
  font-weight: 600;
}

.source-name {
  overflow: hidden;
  color: #555;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.source-score {
  flex-shrink: 0;
  margin-left: auto;
  color: #999;
  font-size: 11px;
}

.source-content {
  margin: 2px 0 0;
  color: #777;
  line-height: 1.5;
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

.pop-username {
  margin: 0 0 8px;
  color: #333;
  font-size: 14px;
  font-weight: 600;
}

.pop-btn {
  width: 100%;
  margin: 0 0 6px;
}

.pop-btn:last-child {
  margin-bottom: 0;
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
  min-width: 88px;
}

/* ---------- 消息操作按钮 ---------- */
.message-actions {
  display: flex;
  gap: 4px;
  margin-top: 6px;
  opacity: 0;
  transition: opacity 0.2s;
}

.message-row.assistant:hover .message-actions {
  opacity: 1;
}

.action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: #666;
  font-size: 14px;
  cursor: pointer;
  transition: background 0.2s, color 0.2s;
}

.action-btn:hover {
  background: #e8e8e8;
  color: #1a7f37;
}

.action-btn.active {
  color: #1a7f37;
  background: #e6f2ea;
}

.token-info {
  margin-left: auto;
  font-size: 12px;
  color: #888;
}

/* ---------- 会话删除确认弹框 ---------- */
.dialog-text {
  margin: 0;
  color: #666;
  font-size: 13px;
  line-height: 1.6;
  word-break: break-all;
}
</style>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import type { UploadRequestOptions } from 'element-plus'

import {
  createKnowledgeBase,
  deleteDocument,
  deleteKnowledgeBase,
  fetchDocuments,
  fetchKnowledgeBases,
  updateKnowledgeBase,
  uploadDocument,
  type KBDocument,
  type KnowledgeBase,
} from '@/api/kb'

const router = useRouter()

/** 新建/重命名共用的表单态；id 为 null 表示新建。 */
interface KbForm {
  id: number | null
  name: string
  description: string
}

const kbs = ref<KnowledgeBase[]>([])
const docs = ref<KBDocument[]>([])
const activeKbId = ref<number | null>(null)
const keyword = ref('')
const loadingKbs = ref(false)
const loadingDocs = ref(false)
const saving = ref(false)
const uploading = ref(false)
const deletingKbId = ref<number | null>(null)
const deletingDocId = ref<number | null>(null)
const errorMsg = ref('')
const form = ref<KbForm | null>(null)
const pendingDelete = ref<KnowledgeBase | null>(null)

/** 入库中的文档轮询定时器：后端在后台切块向量化，前端刷新状态 */
let pollTimer: ReturnType<typeof setTimeout> | null = null
const POLL_MS = 2000

const activeKb = ref<KnowledgeBase | null>(null)

function errText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

function formatTime(value: string): string {
  // 后端返回 "2026-10-21T18:56:00"，展示到分钟即可
  return value.replace('T', ' ').slice(0, 16)
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

const STATUS_LABEL: Record<string, string> = {
  pending: '排队中',
  embedding: '入库中',
  ready: '已就绪',
  failed: '失败',
}

const STATUS_TYPE: Record<string, 'info' | 'warning' | 'success' | 'danger'> = {
  pending: 'info',
  embedding: 'warning',
  ready: 'success',
  failed: 'danger',
}

function stopPolling() {
  if (pollTimer !== null) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

/** 有文档仍在入库时安排下一次刷新。 */
function schedulePolling() {
  stopPolling()
  const busy = docs.value.some((doc) => doc.status === 'pending' || doc.status === 'embedding')
  if (!busy) return
  pollTimer = setTimeout(() => void loadDocs(activeKbId.value), POLL_MS)
}

async function loadKbs(keepSelection = true) {
  loadingKbs.value = true
  errorMsg.value = ''
  try {
    kbs.value = await fetchKnowledgeBases(keyword.value)
    if (!keepSelection) activeKbId.value = null
  } catch (err) {
    errorMsg.value = `知识库列表加载失败：${errText(err)}`
  } finally {
    loadingKbs.value = false
  }
  // 当前选中的库被搜索过滤掉或删除时，同步清空详情面板
  const found = kbs.value.find((item) => item.id === activeKbId.value) ?? null
  activeKb.value = found
  if (!found) {
    stopPolling()
    docs.value = []
  }
}

async function loadDocs(kbId: number | null) {
  if (kbId === null) return
  loadingDocs.value = true
  errorMsg.value = ''
  try {
    const list = await fetchDocuments(kbId)
    // 轮询期间列表可能在请求后才切换库，丢弃过期结果
    if (activeKbId.value !== kbId) return
    docs.value = list
    schedulePolling()
  } catch (err) {
    errorMsg.value = `文档列表加载失败：${errText(err)}`
  } finally {
    loadingDocs.value = false
  }
}

function selectKb(kb: KnowledgeBase) {
  if (loadingDocs.value && activeKbId.value === kb.id) return
  activeKbId.value = kb.id
  activeKb.value = kb
  void loadDocs(kb.id)
}

function onSearch() {
  void loadKbs()
}

// ---------- 新建 / 重命名 ----------
function openCreate() {
  errorMsg.value = ''
  form.value = { id: null, name: '', description: '' }
}

function openEdit(kb: KnowledgeBase) {
  errorMsg.value = ''
  form.value = { id: kb.id, name: kb.name, description: kb.description ?? '' }
}

function closeForm() {
  if (saving.value) return
  form.value = null
}

async function submitForm() {
  const draft = form.value
  if (!draft || saving.value) return
  const name = draft.name.trim()
  if (!name) {
    errorMsg.value = '知识库名称不能为空'
    return
  }

  saving.value = true
  errorMsg.value = ''
  try {
    if (draft.id === null) {
      const created = await createKnowledgeBase(name, draft.description.trim())
      await loadKbs()
      activeKbId.value = created.id
    } else {
      await updateKnowledgeBase(draft.id, name, draft.description.trim())
      await loadKbs()
    }
    form.value = null
    activeKb.value = kbs.value.find((item) => item.id === activeKbId.value) ?? null
  } catch (err) {
    errorMsg.value = errText(err)
  } finally {
    saving.value = false
  }
}

// ---------- 删除知识库 ----------
function askDeleteKb(kb: KnowledgeBase) {
  if (deletingKbId.value !== null) return
  errorMsg.value = ''
  pendingDelete.value = kb
}

function cancelDeleteKb() {
  if (deletingKbId.value !== null) return
  pendingDelete.value = null
}

async function confirmDeleteKb() {
  const kb = pendingDelete.value
  if (!kb || deletingKbId.value !== null) return
  deletingKbId.value = kb.id
  errorMsg.value = ''
  try {
    await deleteKnowledgeBase(kb.id)
    pendingDelete.value = null
    if (activeKbId.value === kb.id) {
      stopPolling()
      docs.value = []
    }
    await loadKbs()
  } catch (err) {
    errorMsg.value = `删除失败：${errText(err)}`
    pendingDelete.value = null
  } finally {
    deletingKbId.value = null
  }
}

// ---------- 文档上传 / 删除 ----------
const MAX_BYTES = 10 * 1024 * 1024
const ALLOWED_EXT = ['txt', 'md', 'pdf', 'docx']

/** el-upload 的自定义上传：走带令牌的 fetch，成功后刷新列表并开启轮询。 */
async function handleUpload(options: UploadRequestOptions) {
  const kbId = activeKbId.value
  if (kbId === null) return
  const file = options.file as File
  const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
  if (!ALLOWED_EXT.includes(ext)) {
    errorMsg.value = `「${file.name}」类型不支持，仅支持 txt / md / pdf / docx`
    return
  }
  if (file.size > MAX_BYTES) {
    errorMsg.value = `「${file.name}」超过 10 MB 上限`
    return
  }

  uploading.value = true
  errorMsg.value = ''
  try {
    const doc = await uploadDocument(kbId, file)
    if (activeKbId.value === kbId) {
      docs.value = [doc, ...docs.value]
      schedulePolling()
      await refreshKbCount(kbId)
    }
  } catch (err) {
    errorMsg.value = `上传失败：${errText(err)}`
  } finally {
    uploading.value = false
  }
}

/** 上传后库内文档数变化：重新拉一次列表让左侧计数准确。 */
async function refreshKbCount(kbId: number) {
  try {
    kbs.value = await fetchKnowledgeBases(keyword.value)
    activeKb.value = kbs.value.find((item) => item.id === kbId) ?? activeKb.value
  } catch {
    /* 计数刷新失败不影响文档列表本身 */
  }
}

async function removeDoc(doc: KBDocument) {
  const kbId = activeKbId.value
  if (kbId === null || deletingDocId.value !== null) return
  deletingDocId.value = doc.id
  errorMsg.value = ''
  try {
    await deleteDocument(kbId, doc.id)
    docs.value = docs.value.filter((item) => item.id !== doc.id)
    schedulePolling()
    await refreshKbCount(kbId)
  } catch (err) {
    errorMsg.value = `删除文档失败：${errText(err)}`
  } finally {
    deletingDocId.value = null
  }
}

function backToChat() {
  void router.push({ name: 'chat' })
}

onMounted(async () => {
  await loadKbs(false)
  // 默认选中第一个库，省去一次点击
  const first = kbs.value[0]
  if (first) selectKb(first)
})

onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="kb-layout">
    <aside class="kb-side">
      <div class="side-head">
        <h2 class="side-title">知识库</h2>
        <el-button class="new-btn" type="primary" plain size="small" @click="openCreate">
          新建
        </el-button>
      </div>

      <input
        v-model="keyword"
        class="search-input"
        type="text"
        placeholder="搜索知识库..."
        @input="onSearch"
      />

      <p v-if="loadingKbs && kbs.length === 0" class="side-empty">加载中…</p>
      <p v-else-if="kbs.length === 0" class="side-empty">
        {{ keyword ? '未找到匹配知识库' : '还没有知识库，点击「新建」创建' }}
      </p>
      <ul v-else class="kb-list">
        <li v-for="kb in kbs" :key="kb.id" class="kb-row" :class="{ active: kb.id === activeKbId }">
          <button class="kb-item" @click="selectKb(kb)">
            <span class="kb-name">{{ kb.name }}</span>
            <span class="kb-meta">{{ kb.doc_count }} 个文档 · {{ formatTime(kb.created_at) }}</span>
          </button>
          <span class="kb-ops">
            <button class="op-btn" title="重命名" @click="openEdit(kb)">✎</button>
            <button
              class="op-btn danger"
              title="删除知识库"
              :disabled="deletingKbId !== null"
              @click="askDeleteKb(kb)"
            >
              ✕
            </button>
          </span>
        </li>
      </ul>

      <div class="side-footer">
        <el-button plain type="primary" size="small" @click="backToChat">返回聊天</el-button>
      </div>
    </aside>

    <main class="kb-main">
      <header class="main-head">
        <div>
          <h1>{{ activeKb ? activeKb.name : '文档管理' }}</h1>
          <p class="subtitle">
            {{
              activeKb?.description
                ? activeKb.description
                : '上传 txt / md / pdf / docx 文档，系统会自动切块并向量化'
            }}
          </p>
        </div>
      </header>

      <p v-if="errorMsg" class="error">{{ errorMsg }}</p>

      <template v-if="activeKb">
        <el-upload
          class="uploader"
          drag
          multiple
          :show-file-list="false"
          :http-request="handleUpload"
          accept=".txt,.md,.pdf,.docx"
        >
          <div class="drop-inner">
            <p class="drop-title">{{ uploading ? '上传中…' : '拖拽文件到此处，或点击选择文件' }}</p>
            <p class="drop-tip">单个文件不超过 10 MB，支持 txt / md / pdf / docx</p>
          </div>
        </el-upload>

        <el-table :data="docs" v-loading="loadingDocs" class="doc-table" empty-text="该知识库还没有文档">
          <el-table-column prop="filename" label="文件名" min-width="180" show-overflow-tooltip />
          <el-table-column label="类型" width="70">
            <template #default="{ row }">{{ row.file_type }}</template>
          </el-table-column>
          <el-table-column label="大小" width="90">
            <template #default="{ row }">{{ formatSize(row.size_bytes) }}</template>
          </el-table-column>
          <el-table-column label="分块" width="70">
            <template #default="{ row }">{{ row.chunk_count }}</template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tooltip
                :content="row.error || '入库失败'"
                :disabled="row.status !== 'failed'"
                placement="top"
              >
                <el-tag :type="STATUS_TYPE[row.status] ?? 'info'" size="small" effect="light">
                  {{ STATUS_LABEL[row.status] ?? row.status }}
                </el-tag>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column label="上传时间" width="130">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="70" align="right" fixed="right">
            <template #default="{ row }">
              <el-button
                link
                type="danger"
                size="small"
                :disabled="deletingDocId !== null"
                @click="removeDoc(row)"
              >
                {{ deletingDocId === row.id ? '…' : '删除' }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>

        <p v-if="docs.some((d) => d.status === 'failed')" class="fail-tip">
          入库失败的文档可删除后重新上传，鼠标悬停「状态」列可查看原因。
        </p>
      </template>

      <p v-else class="main-empty">从左侧选择或新建一个知识库来管理文档。</p>
    </main>

    <!-- 新建 / 重命名 -->
    <el-dialog
      :model-value="form !== null"
      :title="form?.id === null ? '新建知识库' : '重命名知识库'"
      width="420px"
      @close="closeForm"
    >
      <el-form v-if="form" label-position="top" @submit.prevent="submitForm">
        <el-form-item label="名称">
          <el-input v-model="form.name" maxlength="200" placeholder="如：产品手册" />
        </el-form-item>
        <el-form-item label="描述（可选）">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="3"
            maxlength="2000"
            placeholder="这个知识库放什么内容"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="saving" @click="closeForm">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitForm">保存</el-button>
      </template>
    </el-dialog>

    <!-- 删除知识库确认 -->
    <el-dialog
      :model-value="pendingDelete !== null"
      title="删除知识库"
      width="360px"
      @close="cancelDeleteKb"
    >
      <p v-if="pendingDelete" class="dialog-text">
        确定删除「{{ pendingDelete.name }}」？删除后该库不再出现在列表与问答检索中，
        其中的 {{ pendingDelete.doc_count }} 个文档将无法使用。
      </p>
      <template #footer>
        <el-button :disabled="deletingKbId !== null" @click="cancelDeleteKb">取消</el-button>
        <el-button
          type="danger"
          :loading="deletingKbId === pendingDelete?.id"
          :disabled="deletingKbId !== null"
          @click="confirmDeleteKb"
        >
          删除
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.kb-layout {
  display: flex;
  height: 100vh;
  font-family: -apple-system, "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
}

/* ---------- 左侧知识库列表 ---------- */
.kb-side {
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  width: 260px;
  padding: 16px 12px 12px;
  border-right: 1px solid #e5e5e5;
  background: #f7f8fa;
  box-sizing: border-box;
}

.side-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.side-title {
  flex: 1;
  margin: 0;
  color: #333;
  font-size: 16px;
  font-weight: 600;
}

.search-input {
  width: 100%;
  margin-bottom: 10px;
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

.side-empty {
  flex: 1;
  margin-top: 24px;
  text-align: center;
  color: #aaa;
  font-size: 13px;
}

.kb-list {
  flex: 1;
  margin: 0;
  padding: 0;
  list-style: none;
  overflow-y: auto;
}

.kb-row {
  position: relative;
  margin-bottom: 4px;
  border: 1px solid transparent;
  border-radius: 8px;
}

.kb-row:hover {
  background: #eceef1;
}

.kb-row.active {
  border-color: #bcdcc6;
  background: #e6f2ea;
}

.kb-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
  width: 100%;
  padding: 8px 44px 8px 10px;
  border: none;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.kb-name {
  overflow: hidden;
  color: #333;
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kb-meta {
  color: #999;
  font-size: 12px;
}

.kb-ops {
  position: absolute;
  top: 6px;
  right: 4px;
  display: flex;
  gap: 2px;
}

.op-btn {
  width: 20px;
  height: 20px;
  padding: 0;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: #777;
  font-size: 12px;
  line-height: 1;
  opacity: 0.5;
  cursor: pointer;
}

.op-btn:hover:not(:disabled) {
  background: #dfe3e8;
  color: #1a7f37;
  opacity: 1;
}

.op-btn.danger:hover:not(:disabled) {
  background: #fdecec;
  color: #d33;
}

.op-btn:disabled {
  cursor: not-allowed;
  opacity: 0.2;
}

.side-footer {
  flex-shrink: 0;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid #e5e5e5;
}

/* ---------- 右侧文档区 ---------- */
.kb-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 18px 24px;
  background: #fafafa;
  box-sizing: border-box;
  overflow-y: auto;
}

.main-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.main-head h1 {
  margin: 0 0 4px;
  color: #333;
  font-size: 20px;
}

.subtitle {
  margin: 0;
  color: #888;
  font-size: 13px;
}

.error {
  margin: 0 0 10px;
  color: #d33;
  font-size: 13px;
}

.uploader {
  margin-bottom: 14px;
}

.uploader :deep(.el-upload-dragger) {
  padding: 18px;
  border-radius: 10px;
  background: #fff;
}

.uploader :deep(.el-upload-dragger.is-dragover) {
  border-color: #1a7f37;
  background: #f2f9f4;
}

.drop-inner {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.drop-title {
  margin: 0;
  color: #444;
  font-size: 14px;
}

.drop-tip {
  margin: 0;
  color: #999;
  font-size: 12px;
}

.doc-table {
  border-radius: 10px;
  overflow: hidden;
}

.fail-tip {
  margin: 8px 0 0;
  color: #b26a00;
  font-size: 12px;
}

.main-empty {
  margin-top: 80px;
  text-align: center;
  color: #aaa;
  font-size: 14px;
}

.dialog-text {
  margin: 0;
  color: #666;
  font-size: 13px;
  line-height: 1.6;
  word-break: break-all;
}
</style>

<script setup lang="ts">
import { nextTick, ref } from 'vue'

import { sendChat, type ChatMessage } from '@/api/chat'

const messages = ref<ChatMessage[]>([])
const input = ref('')
const loading = ref(false)
const errorMsg = ref('')
const listRef = ref<HTMLElement | null>(null)

async function scrollToBottom() {
  await nextTick()
  listRef.value?.scrollTo({ top: listRef.value.scrollHeight })
}

async function submit() {
  const question = input.value.trim()
  if (!question || loading.value) return

  errorMsg.value = ''
  messages.value.push({ role: 'user', content: question })
  input.value = ''
  loading.value = true
  await scrollToBottom()

  try {
    // 把除最后一条外的历史一并发给后端，让模型拥有上下文
    const history = messages.value.slice(0, -1)
    const { answer } = await sendChat(question, history)
    messages.value.push({ role: 'assistant', content: answer })
  } catch (err) {
    errorMsg.value = err instanceof Error ? err.message : '未知错误'
    input.value = question
  } finally {
    loading.value = false
    await scrollToBottom()
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
  <div class="chat-page">
    <header class="chat-header">
      <h1>问答助手</h1>
      <p class="subtitle">基于 Vue + FastAPI 的基础问答</p>
    </header>

    <div ref="listRef" class="message-list">
      <p v-if="messages.length === 0" class="empty-tip">输入问题开始对话</p>
      <div
        v-for="(msg, index) in messages"
        :key="index"
        class="message-row"
        :class="msg.role"
      >
        <div class="bubble">{{ msg.content }}</div>
      </div>
      <div v-if="loading" class="message-row assistant">
        <div class="bubble thinking">思考中…</div>
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
        {{ loading ? '发送中' : '发送' }}
      </button>
    </footer>
  </div>
</template>

<style scoped>
.chat-page {
  display: flex;
  flex-direction: column;
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

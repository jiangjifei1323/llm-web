<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { login, register } from '@/api/auth'

const router = useRouter()
const mode = ref<'login' | 'register'>('login')
const username = ref('')
const password = ref('')
const loading = ref(false)
const errorMsg = ref('')

function switchMode(next: 'login' | 'register') {
  mode.value = next
  errorMsg.value = ''
}

async function submit() {
  const name = username.value.trim()
  if (!name || !password.value || loading.value) return

  loading.value = true
  errorMsg.value = ''
  try {
    if (mode.value === 'login') {
      await login(name, password.value)
    } else {
      await register(name, password.value)
    }
    await router.replace({ name: 'chat' })
  } catch (err) {
    errorMsg.value = err instanceof Error ? err.message : '操作失败，请重试'
  } finally {
    loading.value = false
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') {
    e.preventDefault()
    void submit()
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <h1>问答助手</h1>
      <p class="subtitle">{{ mode === 'login' ? '登录你的账户' : '注册新账户' }}</p>

      <div class="tabs">
        <button
          class="tab"
          :class="{ active: mode === 'login' }"
          @click="switchMode('login')"
        >
          登录
        </button>
        <button
          class="tab"
          :class="{ active: mode === 'register' }"
          @click="switchMode('register')"
        >
          注册
        </button>
      </div>

      <form class="login-form" @submit.prevent="submit">
        <label>
          用户名
          <input
            v-model="username"
            type="text"
            autocomplete="username"
            placeholder="请输入用户名"
            @keydown="onKeydown"
          />
        </label>
        <label>
          密码
          <input
            v-model="password"
            type="password"
            :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
            :placeholder="mode === 'login' ? '请输入密码' : '至少 4 位'"
            @keydown="onKeydown"
          />
        </label>

        <p v-if="errorMsg" class="error">{{ errorMsg }}</p>

        <button
          class="submit-btn"
          type="submit"
          :disabled="loading || !username.trim() || !password"
        >
          {{ loading ? '请稍候…' : mode === 'login' ? '登录' : '注册并登录' }}
        </button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background: #f6f7f9;
  padding: 16px;
  box-sizing: border-box;
}

.login-card {
  width: 100%;
  max-width: 360px;
  background: #fff;
  border: 1px solid #e5e5e5;
  border-radius: 12px;
  padding: 28px 24px;
  box-sizing: border-box;
  text-align: center;
}

.login-card h1 {
  margin: 0;
  font-size: 22px;
}

.subtitle {
  margin: 6px 0 18px;
  color: #888;
  font-size: 13px;
}

.tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 18px;
}

.tab {
  flex: 1;
  padding: 8px 0;
  border: 1px solid #d0d0d0;
  border-radius: 8px;
  background: #fff;
  color: #555;
  font-size: 14px;
  cursor: pointer;
}

.tab.active {
  border-color: #1a7f37;
  background: #1a7f37;
  color: #fff;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
  text-align: left;
}

.login-form label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: #555;
}

.login-form input {
  padding: 10px 12px;
  border: 1px solid #d0d0d0;
  border-radius: 8px;
  font-size: 15px;
  box-sizing: border-box;
}

.login-form input:focus {
  outline: none;
  border-color: #1a7f37;
}

.error {
  margin: 0;
  color: #d33;
  font-size: 13px;
}

.submit-btn {
  padding: 10px 0;
  border: none;
  border-radius: 8px;
  background: #1a7f37;
  color: #fff;
  font-size: 15px;
  cursor: pointer;
}

.submit-btn:disabled {
  background: #b5cdbd;
  cursor: not-allowed;
}
</style>

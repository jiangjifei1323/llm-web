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

      <el-radio-group v-model="mode" class="mode-switch" @change="errorMsg = ''">
        <el-radio-button value="login">登录</el-radio-button>
        <el-radio-button value="register">注册</el-radio-button>
      </el-radio-group>

      <form class="login-form" @submit.prevent="submit">
        <el-input
          v-model="username"
          size="large"
          autocomplete="username"
          placeholder="请输入用户名"
          @keydown="onKeydown"
        >
          <template #prefix>
            <el-icon><User /></el-icon>
          </template>
        </el-input>

        <el-input
          v-model="password"
          type="password"
          size="large"
          show-password
          :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
          :placeholder="mode === 'login' ? '请输入密码' : '至少 4 位'"
          @keydown="onKeydown"
        >
          <template #prefix>
            <el-icon><Lock /></el-icon>
          </template>
        </el-input>

        <p v-if="errorMsg" class="error">{{ errorMsg }}</p>

        <el-button
          type="primary"
          size="large"
          native-type="submit"
          class="submit-btn"
          :loading="loading"
          loading-text="请稍候…"
          :disabled="!username.trim() || !password"
        >
          {{ mode === 'login' ? '登录' : '注册并登录' }}
        </el-button>
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

.mode-switch {
  margin-bottom: 20px;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
  text-align: left;
}

.submit-btn {
  width: 100%;
}

.error {
  margin: 0;
  color: var(--el-color-danger);
  font-size: 13px;
}
</style>

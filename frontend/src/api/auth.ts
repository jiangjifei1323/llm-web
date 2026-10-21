/** 登录态管理：token 存 localStorage，请求统一附带 Authorization 头。 */

const TOKEN_KEY = 'llm_token'
const USER_KEY = 'llm_username'
const ADMIN_KEY = 'llm_is_admin'

export interface AuthResult {
  access_token: string
  token_type: string
  username: string
  is_admin: boolean
}

export interface MeResult {
  username: string
  is_admin: boolean
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function getUsername(): string | null {
  return localStorage.getItem(USER_KEY)
}

/** 当前登录用户是否为管理员（后台管理入口与 /admin 路由据此判定）。 */
export function isAdmin(): boolean {
  return localStorage.getItem(ADMIN_KEY) === '1'
}

export function saveAuth(result: AuthResult): void {
  localStorage.setItem(TOKEN_KEY, result.access_token)
  localStorage.setItem(USER_KEY, result.username)
  localStorage.setItem(ADMIN_KEY, result.is_admin ? '1' : '')
}

export function clearAuth(): void {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
  localStorage.removeItem(ADMIN_KEY)
}

/** 401 时清除登录态并跳转登录页（供各 API 调用复用）。 */
export function handleUnauthorized(): void {
  clearAuth()
  if (window.location.pathname !== '/login') {
    window.location.href = '/login'
  }
}

async function postAuth(path: string, username: string, password: string): Promise<AuthResult> {
  const resp = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  const data = (await resp.json().catch(() => null)) as
    | (AuthResult & { detail?: string })
    | null
  if (!resp.ok || !data || !data.access_token) {
    throw new Error(data?.detail ?? `请求失败（${resp.status}）`)
  }
  saveAuth(data)
  return data
}

export function login(username: string, password: string): Promise<AuthResult> {
  return postAuth('/api/auth/login', username, password)
}

export function register(username: string, password: string): Promise<AuthResult> {
  return postAuth('/api/auth/register', username, password)
}

/**
 * 读取当前令牌对应的用户信息，并把用户名与管理员标识同步进 localStorage。
 * 登录前写入的标识可能已过期（例如管理员权限变更），页面挂载时调用可纠正。
 */
export async function fetchMe(): Promise<MeResult> {
  const token = getToken()
  const resp = await fetch('/api/auth/me', {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (resp.status === 401) {
    handleUnauthorized()
    throw new Error('登录已过期，请重新登录')
  }
  if (!resp.ok) {
    throw new Error(`获取用户信息失败（${resp.status}）`)
  }
  const data = (await resp.json()) as MeResult
  saveAuth({
    access_token: token ?? '',
    token_type: 'bearer',
    username: data.username,
    is_admin: data.is_admin,
  })
  return data
}

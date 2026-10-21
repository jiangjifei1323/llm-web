/** 登录态管理：token 存 localStorage，请求统一附带 Authorization 头。 */

const TOKEN_KEY = 'llm_token'
const USER_KEY = 'llm_username'

export interface AuthResult {
  access_token: string
  token_type: string
  username: string
  is_admin: boolean
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function getUsername(): string | null {
  return localStorage.getItem(USER_KEY)
}

export function saveAuth(result: AuthResult): void {
  localStorage.setItem(TOKEN_KEY, result.access_token)
  localStorage.setItem(USER_KEY, result.username)
}

export function clearAuth(): void {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
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

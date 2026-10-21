import { getToken, handleUnauthorized } from './auth'

/** 带令牌的 JSON 请求：401 视为登录失效，其余非 2xx 抛出后端 detail。 */
export async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken()
  const resp = await fetch(path, {
    ...init,
    headers: {
      ...init?.headers,
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  })
  if (resp.status === 401) {
    handleUnauthorized()
    throw new Error('登录已过期，请重新登录')
  }
  if (!resp.ok) {
    const raw = await resp.text().catch(() => '')
    let detail = raw
    try {
      detail = (JSON.parse(raw) as { detail?: string }).detail ?? raw
    } catch {
      // 非 JSON 响应，直接使用原文
    }
    throw new Error(`请求失败（${resp.status}）：${detail}`)
  }
  return (await resp.json()) as T
}

import { getToken, handleUnauthorized } from './auth'
import { requestJson } from './http'

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

/** 左侧会话列表的一条摘要 */
export interface ConversationSummary {
  id: string
  title: string
  updated_at: string
}

/** 当前用户的会话列表，按最近更新倒序。 */
export function fetchConversations(): Promise<ConversationSummary[]> {
  return requestJson<ConversationSummary[]>('/api/conversations')
}

/** 某个会话的历史消息（role/content 序列）。 */
export function fetchConversationMessages(conversationId: string): Promise<ChatMessage[]> {
  return requestJson<ChatMessage[]>(`/api/conversations/${conversationId}/messages`)
}

/** 修改会话名称，返回后端保存后的名称（已去空白、截断）。 */
export function renameConversation(conversationId: string, title: string): Promise<string> {
  return requestJson<{ id: string; title: string }>(`/api/conversations/${conversationId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  }).then((data) => data.title)
}

/** 软删除会话（后端只打删除标记，数据保留），返回被删除的会话 id。 */
export function deleteConversation(conversationId: string): Promise<string> {
  return requestJson<{ id: string; deleted: boolean }>(`/api/conversations/${conversationId}`, {
    method: 'DELETE',
  }).then((data) => data.id)
}

export interface StreamChatOptions {
  /** 会话 id，用于后端把问答落库并延续上下文；首轮可传 null */
  conversationId?: string | null
  onDelta: (delta: string) => void
  /** 首条 meta 事件回调（模型名、后端生成或沿用的会话 id） */
  onMeta?: (meta: { model?: string; conversationId?: string }) => void
}

/**
 * 发起流式问答（SSE）：每收到一个文本增量块调用 onDelta，
 * 收到 [DONE] 后 resolve；后端以 error 事件报错时 reject。
 */
export async function streamChat(
  message: string,
  history: ChatMessage[],
  options: StreamChatOptions,
): Promise<void> {
  const { conversationId, onDelta, onMeta } = options
  const token = getToken()
  const resp = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      message,
      history,
      ...(conversationId ? { conversation_id: conversationId } : {}),
    }),
  })

  if (resp.status === 401) {
    handleUnauthorized()
    throw new Error('登录已过期，请重新登录')
  }

  if (!resp.ok || !resp.body) {
    const raw = await resp.text().catch(() => '')
    let detail = raw
    try {
      detail = (JSON.parse(raw) as { detail?: string }).detail ?? raw
    } catch {
      // 非 JSON 响应，直接使用原文
    }
    throw new Error(`请求失败（${resp.status}）：${detail}`)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // 逐个取出完整的 SSE 事件（以空行分隔），不完整的内容留在缓冲区
    let sep: number
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const rawEvent = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)

      const dataLines = rawEvent
        .split('\n')
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trim())
      if (dataLines.length === 0) continue

      const payload = dataLines.join('\n')
      if (payload === '[DONE]') return

      const parsed = JSON.parse(payload) as {
        delta?: string
        error?: string
        model?: string
        conversation_id?: string
      }
      if (parsed.error) throw new Error(parsed.error)
      if (parsed.model || parsed.conversation_id) {
        onMeta?.({ model: parsed.model, conversationId: parsed.conversation_id })
      }
      if (parsed.delta) onDelta(parsed.delta)
    }
  }
}

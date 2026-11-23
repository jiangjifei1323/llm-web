import { getToken, handleUnauthorized } from './auth'
import { requestJson } from './http'
import type { ChatSource } from './chat'

export type AgentMode = 'auto' | 'agent' | 'chat'

export interface AgentTaskCreateResult {
  task_id: string | null
  conversation_id: string
  status: string | null
  mode: 'chat' | 'agent'
}

export interface AgentTaskView {
  id: string
  conversation_id: string
  status: string
  mode: string
  plan?: unknown
  error?: string | null
  result_message_id?: number | null
  used_llm_calls: number
  used_tool_calls: number
  used_tokens: number
}

export interface AgentEventHandlers {
  signal?: AbortSignal
  onEvent?: (type: string, data: Record<string, unknown>) => void
  onDelta?: (delta: string) => void
  onCompleted?: (data: {
    messageId?: number
    inputTokens?: number
    outputTokens?: number
    totalTokens?: number
    sources?: ChatSource[]
  }) => void
  onFailed?: (error: string) => void
}

export function createAgentTask(body: {
  message: string
  conversation_id?: string | null
  kb_ids?: number[]
  mode?: AgentMode
}): Promise<AgentTaskCreateResult> {
  return requestJson<AgentTaskCreateResult>('/api/agent/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message: body.message,
      mode: body.mode ?? 'auto',
      ...(body.conversation_id ? { conversation_id: body.conversation_id } : {}),
      ...(body.kb_ids?.length ? { kb_ids: body.kb_ids } : {}),
    }),
  })
}

export function cancelAgentTask(taskId: string): Promise<void> {
  return requestJson(`/api/agent/tasks/${taskId}/cancel`, { method: 'POST' }).then(
    () => undefined,
  )
}

export type ApprovalAction = 'approve' | 'reject' | 'edit'

export function resolveAgentApproval(
  taskId: string,
  body: { action: ApprovalAction; args?: Record<string, unknown>; reason?: string },
): Promise<{ id: string; status: string; action: string }> {
  return requestJson(`/api/agent/tasks/${taskId}/approvals`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function downloadAgentAudit(taskId: string): Promise<void> {
  const token = getToken()
  return fetch(`/api/agent/tasks/${taskId}/audit`, {
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  }).then(async (resp) => {
    if (resp.status === 401) {
      handleUnauthorized()
      throw new Error('登录已过期，请重新登录')
    }
    if (!resp.ok) {
      throw new Error(`导出审计失败（${resp.status}）`)
    }
    const blob = await resp.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `agent-audit-${taskId}.json`
    a.click()
    URL.revokeObjectURL(url)
  })
}

export function fetchAgentTask(taskId: string): Promise<AgentTaskView> {
  return requestJson<AgentTaskView>(`/api/agent/tasks/${taskId}`)
}

/**
 * 订阅任务事件流。断线不取消任务；调用方 abort 仅断开订阅。
 * 若需要取消任务，请另行调用 cancelAgentTask。
 */
export async function subscribeAgentEvents(
  taskId: string,
  handlers: AgentEventHandlers,
): Promise<void> {
  const token = getToken()
  const resp = await fetch(`/api/agent/tasks/${taskId}/events`, {
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    signal: handlers.signal,
  })

  if (resp.status === 401) {
    handleUnauthorized()
    throw new Error('登录已过期，请重新登录')
  }
  if (!resp.ok || !resp.body) {
    const raw = await resp.text().catch(() => '')
    throw new Error(`订阅任务事件失败（${resp.status}）：${raw}`)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let sep: number
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const rawEvent = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)

      if (rawEvent.startsWith(':')) continue // heartbeat

      let eventType = ''
      const dataLines: string[] = []
      for (const line of rawEvent.split('\n')) {
        if (line.startsWith('event:')) eventType = line.slice(6).trim()
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
      }
      if (!dataLines.length) continue

      const parsed = JSON.parse(dataLines.join('\n')) as Record<string, unknown>
      const type = eventType || String(parsed.type || '')
      handlers.onEvent?.(type, parsed)

      if (type === 'message.delta' && typeof parsed.delta === 'string') {
        handlers.onDelta?.(parsed.delta)
      }
      if (type === 'task.completed') {
        handlers.onCompleted?.({
          messageId: typeof parsed.message_id === 'number' ? parsed.message_id : undefined,
          inputTokens: typeof parsed.input_tokens === 'number' ? parsed.input_tokens : undefined,
          outputTokens: typeof parsed.output_tokens === 'number' ? parsed.output_tokens : undefined,
          totalTokens: typeof parsed.total_tokens === 'number' ? parsed.total_tokens : undefined,
        })
        return
      }
      if (type === 'task.failed' || type === 'task.cancelled') {
        const err =
          typeof parsed.error === 'string'
            ? parsed.error
            : type === 'task.cancelled'
              ? '任务已取消'
              : '任务失败'
        handlers.onFailed?.(err)
        return
      }
      // approval.required / approval.resolved：不断开订阅，由 onEvent 处理
    }
  }
}

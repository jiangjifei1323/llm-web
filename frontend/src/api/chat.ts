export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface ChatResult {
  answer: string
  model: string
}

/** 发送一次问答：message 为当前问题，history 为此前对话 */
export async function sendChat(message: string, history: ChatMessage[]): Promise<ChatResult> {
  const resp = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, history }),
  })

  if (!resp.ok) {
    const detail = await resp.text()
    throw new Error(`请求失败（${resp.status}）：${detail}`)
  }

  return resp.json() as Promise<ChatResult>
}

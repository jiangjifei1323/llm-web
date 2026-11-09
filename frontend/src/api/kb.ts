import { requestJson } from './http'

/** 一个知识库：doc_count 为未删除的文档数 */
export interface KnowledgeBase {
  id: number
  name: string
  description: string | null
  doc_count: number
  created_at: string
}

/** 知识库中的一份文档；status: pending -> embedding -> ready / failed */
export interface KBDocument {
  id: number
  kb_id: number
  filename: string
  file_type: string
  size_bytes: number
  status: 'pending' | 'embedding' | 'ready' | 'failed'
  error: string | null
  chunk_count: number
  created_at: string
}

/** 问答请求携带的知识库上限（与后端 ChatRequest.kb_ids 校验一致） */
export const MAX_KB_PER_CHAT = 10

/** 知识库列表，可按名称搜索。 */
export function fetchKnowledgeBases(keyword?: string): Promise<KnowledgeBase[]> {
  const params = keyword ? `?keyword=${encodeURIComponent(keyword)}` : ''
  return requestJson<KnowledgeBase[]>(`/api/kb${params}`)
}

/** 新建知识库。 */
export function createKnowledgeBase(name: string, description?: string): Promise<KnowledgeBase> {
  return requestJson<KnowledgeBase>('/api/kb', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description: description || null }),
  })
}

/** 修改知识库名称与描述。 */
export function updateKnowledgeBase(
  id: number,
  name: string,
  description?: string,
): Promise<KnowledgeBase> {
  return requestJson<KnowledgeBase>(`/api/kb/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description: description || null }),
  })
}

/** 软删除知识库，返回被删除的库 id。 */
export function deleteKnowledgeBase(id: number): Promise<number> {
  return requestJson<{ id: number; deleted: boolean }>(`/api/kb/${id}`, {
    method: 'DELETE',
  }).then((data) => data.id)
}

/** 某个知识库的文档列表（含入库状态，供轮询进度）。 */
export function fetchDocuments(kbId: number): Promise<KBDocument[]> {
  return requestJson<KBDocument[]>(`/api/kb/${kbId}/documents`)
}

/** 上传一份文档：后端立即返回 pending，入库在后台进行。 */
export function uploadDocument(kbId: number, file: File): Promise<KBDocument> {
  const form = new FormData()
  form.append('file', file)
  // 不设置 Content-Type，交给浏览器带 multipart 边界
  return requestJson<KBDocument>(`/api/kb/${kbId}/documents`, { method: 'POST', body: form })
}

/** 删除文档（向量块级联删除），返回被删除的文档 id。 */
export function deleteDocument(kbId: number, docId: number): Promise<number> {
  return requestJson<{ id: number; deleted: boolean }>(`/api/kb/${kbId}/documents/${docId}`, {
    method: 'DELETE',
  }).then((data) => data.id)
}

import { requestJson } from './http'

/** 后台用户管理列表的一项 */
export interface AdminUser {
  id: number
  username: string
  is_admin: boolean
  created_at: string
}

export interface AdminUserCreatePayload {
  username: string
  password: string
  is_admin: boolean
}

export interface AdminUserUpdatePayload {
  username?: string
  /** 留空表示不修改密码 */
  password?: string
  is_admin?: boolean
}

const jsonHeaders = { 'Content-Type': 'application/json' }

/** 系统所有用户（仅管理员可调）。 */
export function fetchUsers(): Promise<AdminUser[]> {
  return requestJson<AdminUser[]>('/api/admin/users')
}

/** 新建用户。 */
export function createUser(payload: AdminUserCreatePayload): Promise<AdminUser> {
  return requestJson<AdminUser>('/api/admin/users', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(payload),
  })
}

/** 修改用户名 / 重置密码 / 切换管理员身份。 */
export function updateUser(id: number, payload: AdminUserUpdatePayload): Promise<AdminUser> {
  return requestJson<AdminUser>(`/api/admin/users/${id}`, {
    method: 'PATCH',
    headers: jsonHeaders,
    body: JSON.stringify(payload),
  })
}

/** 删除用户（后端会一并软删除其会话），返回被删除的用户 id。 */
export function deleteUser(id: number): Promise<number> {
  return requestJson<{ id: number; deleted: boolean }>(`/api/admin/users/${id}`, {
    method: 'DELETE',
  }).then((data) => data.id)
}

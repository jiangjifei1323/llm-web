"""后台用户管理接口（仅管理员可用）。"""

from fastapi import APIRouter, Depends, HTTPException

from .. import auth, db
from ..db import User
from ..schemas.user import AdminUser, AdminUserCreate, AdminUserUpdate, DeletedUser

router = APIRouter(prefix="/api/admin", tags=["后台管理"])


def _user_row(user: User) -> AdminUser:
    return AdminUser(
        id=user.id, username=user.username, is_admin=user.is_admin, created_at=user.created_at
    )


@router.get("/users", response_model=list[AdminUser])
def admin_list_users(_: User = Depends(auth.require_admin)) -> list[AdminUser]:
    """系统所有用户（含管理员），按 id 正序。"""
    return [AdminUser(**item) for item in db.list_users()]


@router.post("/users", response_model=AdminUser, status_code=201)
def admin_create_user(req: AdminUserCreate, _: User = Depends(auth.require_admin)) -> AdminUser:
    """新建用户，可直接指定管理员身份（无需自动登录）。"""
    if db.is_username_taken(req.username):
        raise HTTPException(status_code=409, detail="用户名已存在")
    user = db.create_user(req.username, auth.hash_password(req.password), is_admin=req.is_admin)
    return _user_row(user)


@router.patch("/users/{user_id}", response_model=AdminUser)
def admin_update_user(
    user_id: int, req: AdminUserUpdate, _: User = Depends(auth.require_admin)
) -> AdminUser:
    """修改用户名 / 重置密码 / 切换管理员身份。"""
    target = db.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if req.username is None and req.password is None and req.is_admin is None:
        raise HTTPException(status_code=422, detail="没有需要修改的字段")
    if req.username is not None and req.username != target.username:
        if db.is_username_taken(req.username, exclude_id=user_id):
            raise HTTPException(status_code=409, detail="用户名已存在")
    if req.is_admin is False and target.is_admin and db.count_admins() <= 1:
        raise HTTPException(status_code=409, detail="系统至少需要保留一个管理员")
    db.update_user(
        user_id,
        username=req.username,
        password_hash=auth.hash_password(req.password) if req.password else None,
        is_admin=req.is_admin,
    )
    updated = db.get_user_by_id(user_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return _user_row(updated)


@router.delete("/users/{user_id}", response_model=DeletedUser)
def admin_delete_user(
    user_id: int, operator: User = Depends(auth.require_admin)
) -> DeletedUser:
    """删除用户（其会话一并软删除，数据保留在库内）。"""
    target = db.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user_id == operator.id:
        raise HTTPException(status_code=409, detail="不能删除当前登录的账号")
    if target.is_admin and db.count_admins() <= 1:
        raise HTTPException(status_code=409, detail="系统至少需要保留一个管理员")
    if not db.delete_user(user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    return DeletedUser(id=user_id, deleted=True)

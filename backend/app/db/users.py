"""用户表的数据访问（注册、后台管理与鉴权查询）。"""

from typing import Any

from sqlalchemy import func, select, update

from .base import SessionLocal, _now
from .models import Conversation, User


def get_user_by_username(username: str) -> User | None:
    """按用户名取活跃用户；已软删除的一律视为不存在（调用方无需再判断删除态）。"""
    with SessionLocal() as session:
        return session.scalar(
            select(User).where(User.username == username, User.deleted_at.is_(None))
        )


def create_user(username: str, password_hash: str, is_admin: bool = False) -> User:
    with SessionLocal() as session:
        user = User(username=username, password_hash=password_hash, is_admin=is_admin)
        session.add(user)
        session.commit()
        return user


def list_users() -> list[dict[str, Any]]:
    """后台用户管理用：全部活跃用户（含管理员，排除已软删除），按 id 正序。"""
    with SessionLocal() as session:
        users = session.execute(
            select(User)
            .where(User.deleted_at.is_(None))
            .order_by(User.id)
        ).scalars().all()
        return [
            {
                "id": user.id,
                "username": user.username,
                "is_admin": user.is_admin,
                "created_at": user.created_at,
            }
            for user in users
        ]


def get_user_by_id(user_id: int) -> User | None:
    """按 id 取活跃用户；已软删除的视为不存在。"""
    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None or user.deleted_at is not None:
            return None
        return user


def is_username_taken(username: str, exclude_id: int | None = None) -> bool:
    """用户名是否已被占用；改名时通过 exclude_id 排除自己。"""
    with SessionLocal() as session:
        stmt = select(User.id).where(User.username == username)
        if exclude_id is not None:
            stmt = stmt.where(User.id != exclude_id)
        return session.scalar(stmt) is not None


def count_admins() -> int:
    """活跃管理员数量：用于兜住「至少保留一个管理员」的护栏（已软删除的不计）。"""
    with SessionLocal() as session:
        return session.scalar(
            select(func.count(User.id)).where(
                User.is_admin.is_(True), User.deleted_at.is_(None)
            )
        ) or 0


def update_user(
    user_id: int,
    *,
    username: str | None = None,
    password_hash: str | None = None,
    is_admin: bool | None = None,
) -> None:
    """按需更新用户名 / 密码 / 管理员标识，值为 None 表示不改这一项。"""
    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None:
            return
        if username is not None:
            user.username = username
        if password_hash is not None:
            user.password_hash = password_hash
        if is_admin is not None:
            user.is_admin = is_admin
        session.commit()


def delete_user(user_id: int) -> bool:
    """软删除用户，同时软删除其名下未删除的会话；已删除的幂等返回 False。

    保留物理行有两个好处：用户名仍被占用，注册/复活时不会撞唯一约束；会话与
    模型调用记录的外键归属也不会因删除而丢失。这里仍一并给会话打删除标记，
    是为了让被删用户的会话对其不可见，也不会被 claim_conversation 归属给他人。
    """
    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None or user.deleted_at is not None:
            return False
        user.deleted_at = _now()
        session.execute(
            update(Conversation)
            .where(Conversation.user_id == user_id, Conversation.deleted_at.is_(None))
            .values(deleted_at=_now())
        )
        session.commit()
        return True


def reactivate_user_by_username(username: str) -> User | None:
    """复活被软删除的同名用户（清除删除标记）；不存在软删行时返回 None。

    供 seed_admin 在启动时使用：admin 账号被软删后若不复活，直接重建会因
    username 唯一约束崩溃。
    """
    with SessionLocal() as session:
        user = session.scalar(
            select(User).where(User.username == username, User.deleted_at.is_not(None))
        )
        if user is None:
            return None
        user.deleted_at = None
        session.commit()
        return user

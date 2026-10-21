"""用户表的数据访问（注册、后台管理与鉴权查询）。"""

from typing import Any

from sqlalchemy import func, select, update

from .base import SessionLocal, _now
from .models import Conversation, User


def get_user_by_username(username: str) -> User | None:
    with SessionLocal() as session:
        return session.scalar(select(User).where(User.username == username))


def create_user(username: str, password_hash: str, is_admin: bool = False) -> User:
    with SessionLocal() as session:
        user = User(username=username, password_hash=password_hash, is_admin=is_admin)
        session.add(user)
        session.commit()
        return user


def list_users() -> list[dict[str, Any]]:
    """后台用户管理用：全部用户（含管理员），按 id 正序。"""
    with SessionLocal() as session:
        users = session.execute(select(User).order_by(User.id)).scalars().all()
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
    with SessionLocal() as session:
        return session.get(User, user_id)


def is_username_taken(username: str, exclude_id: int | None = None) -> bool:
    """用户名是否已被占用；改名时通过 exclude_id 排除自己。"""
    with SessionLocal() as session:
        stmt = select(User.id).where(User.username == username)
        if exclude_id is not None:
            stmt = stmt.where(User.id != exclude_id)
        return session.scalar(stmt) is not None


def count_admins() -> int:
    """管理员数量：用于兜住「至少保留一个管理员」的护栏。"""
    with SessionLocal() as session:
        return session.scalar(select(func.count(User.id)).where(User.is_admin.is_(True))) or 0


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
    """删除用户，同时软删除其名下未删除的会话。

    会话外键是 ON DELETE SET NULL，若不先打删除标记，被删用户的会话会变成
    无主会话，后续聊天时可能被 claim_conversation 归属给其他用户。
    """
    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None:
            return False
        session.execute(
            update(Conversation)
            .where(Conversation.user_id == user_id, Conversation.deleted_at.is_(None))
            .values(deleted_at=_now())
        )
        session.delete(user)
        session.commit()
        return True

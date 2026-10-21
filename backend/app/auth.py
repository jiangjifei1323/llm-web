"""用户认证：bcrypt 密码哈希 + JWT 签发/校验 + FastAPI 登录依赖。"""

import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import db

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")
JWT_ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "24"))

_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_token(user: db.User) -> str:
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "is_admin": user.is_admin,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> db.User:
    """从 Authorization: Bearer <token> 解析当前用户，失败返回 401。"""
    if credentials is None:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录") from None
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="无效的登录凭证") from None
    user = db.get_user_by_username(payload.get("username", ""))
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


def seed_admin(username: str = "admin", password: str = "admin") -> None:
    """启动时确保管理员账户存在（已存在则不覆盖，保留改过的密码）。"""
    if db.get_user_by_username(username) is None:
        db.create_user(username, hash_password(password), is_admin=True)

"""用户认证：bcrypt 密码哈希 + JWT 签发/校验 + FastAPI 登录依赖。"""

import logging
import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import db

# 源码里公开可见的开发兜底密钥，生产环境禁止使用
DEFAULT_JWT_SECRET = "dev-secret-change-me"
# 密钥最短长度（HS256 建议至少 256 bit）
MIN_SECRET_LENGTH = 32
JWT_SECRET = os.getenv("JWT_SECRET", DEFAULT_JWT_SECRET)
JWT_ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "24"))
# 部署环境标识，production/prod 时对密钥做强制校验
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()

_bearer = HTTPBearer(auto_error=False)
_logger = logging.getLogger(__name__)


def ensure_secret_configured() -> None:
    """启动校验 JWT 密钥：生产环境用默认密钥或过短密钥直接拒绝启动。

    开发环境只打印告警并保留兜底密钥，避免本地起服务还要先配密钥。
    """
    if JWT_SECRET == DEFAULT_JWT_SECRET:
        problem = "仍在使用源码内置的默认密钥"
    elif len(JWT_SECRET) < MIN_SECRET_LENGTH:
        problem = f"长度仅 {len(JWT_SECRET)} 字符，建议不少于 {MIN_SECRET_LENGTH}"
    else:
        return
    if APP_ENV in ("production", "prod"):
        raise RuntimeError(
            f"JWT_SECRET 配置不安全（{problem}），生产环境请在 backend/.env 中"
            "配置随机长密钥后重新启动"
        )
    _logger.warning("JWT_SECRET %s，仅适用于本地开发环境", problem)


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


def require_admin(user: db.User = Depends(get_current_user)) -> db.User:
    """后台接口专用依赖：非管理员一律 403。"""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def seed_admin(username: str = "admin", password: str = "admin") -> None:
    """启动时确保管理员账户存在（已存在则不覆盖，保留改过的密码）。

    活跃账号已存在直接返回；若同名账号被软删除过则复活它（沿用原密码哈希），
    否则直接 create 会撞 username 唯一约束导致启动崩溃。
    """
    if db.get_user_by_username(username) is not None:
        return
    if db.reactivate_user_by_username(username) is None:
        db.create_user(username, hash_password(password), is_admin=True)

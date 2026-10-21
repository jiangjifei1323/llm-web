"""认证接口：注册、登录与当前用户信息。"""

from fastapi import APIRouter, Depends, HTTPException

from .. import auth, db
from ..db import User
from ..schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserInfo

router = APIRouter(prefix="/api/auth", tags=["认证"])


def _token_response(user: User) -> TokenResponse:
    return TokenResponse(
        access_token=auth.create_token(user), username=user.username, is_admin=user.is_admin
    )


@router.post("/register", response_model=TokenResponse)
def register(req: RegisterRequest) -> TokenResponse:
    """注册普通用户，成功后直接返回令牌（自动登录）。"""
    if db.get_user_by_username(req.username) is not None:
        raise HTTPException(status_code=409, detail="用户名已存在")
    user = db.create_user(req.username, auth.hash_password(req.password))
    return _token_response(user)


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest) -> TokenResponse:
    user = db.get_user_by_username(req.username)
    if user is None or not auth.verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return _token_response(user)


@router.get("/me", response_model=UserInfo)
def me(user: User = Depends(auth.get_current_user)) -> UserInfo:
    return UserInfo(username=user.username, is_admin=user.is_admin)

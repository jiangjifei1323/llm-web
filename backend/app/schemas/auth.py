"""认证模块的请求/响应模型。"""

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=64)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=32, pattern=r"^[\w\u4e00-\u9fa5-]+$")
    password: str = Field(min_length=4, max_length=64)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    is_admin: bool


class UserInfo(BaseModel):
    username: str
    is_admin: bool

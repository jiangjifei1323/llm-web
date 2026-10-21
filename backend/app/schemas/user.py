"""后台用户管理模块的请求/响应模型。"""

from datetime import datetime

from pydantic import BaseModel, Field

USERNAME_PATTERN = r"^[\w\u4e00-\u9fa5-]+$"


class AdminUser(BaseModel):
    """后台用户列表的一项。"""

    id: int
    username: str
    is_admin: bool
    created_at: datetime


class AdminUserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=32, pattern=USERNAME_PATTERN)
    password: str = Field(min_length=4, max_length=64)
    is_admin: bool = False


class AdminUserUpdate(BaseModel):
    """按需提交要改的字段：不传的字段保持原值。"""

    username: str | None = Field(
        default=None, min_length=2, max_length=32, pattern=USERNAME_PATTERN
    )
    password: str | None = Field(default=None, min_length=4, max_length=64)
    is_admin: bool | None = None


class DeletedUser(BaseModel):
    id: int
    deleted: bool

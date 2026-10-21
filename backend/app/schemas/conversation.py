"""会话管理模块的请求/响应模型。"""

from datetime import datetime

from pydantic import BaseModel, Field


class ConversationSummary(BaseModel):
    """左侧会话列表用的一条摘要。"""

    id: str
    title: str
    updated_at: datetime


class RenameRequest(BaseModel):
    """修改会话名称。"""

    title: str = Field(min_length=1, max_length=200)


class RenamedConversation(BaseModel):
    """改名后的会话，供前端就地更新列表项。"""

    id: str
    title: str


class DeletedConversation(BaseModel):
    """删除结果，供前端从列表中移除对应项。"""

    id: str
    deleted: bool

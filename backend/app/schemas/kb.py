"""知识库模块的请求/响应模型。"""

from datetime import datetime

from pydantic import BaseModel, Field


class KBCreateRequest(BaseModel):
    """新建知识库。"""

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class KBUpdateRequest(BaseModel):
    """修改知识库名称与描述。"""

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class KnowledgeBaseOut(BaseModel):
    """知识库列表/详情返回的一条记录。"""

    id: int
    name: str
    description: str | None
    doc_count: int = 0
    created_at: datetime


class DeletedKnowledgeBase(BaseModel):
    """删除结果，供前端从列表中移除对应项。"""

    id: int
    deleted: bool


class DocumentOut(BaseModel):
    """文档一条记录：status 供前端轮询入库进度。"""

    id: int
    kb_id: int
    filename: str
    file_type: str
    size_bytes: int
    status: str
    error: str | None
    chunk_count: int
    created_at: datetime

"""问答模块的请求/响应模型。"""

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant|system)$")
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=40)
    conversation_id: str | None = None
    # 本轮问答要检索的知识库 id 列表；为空则完全不走 RAG
    kb_ids: list[int] = Field(default_factory=list, max_length=10)


class ChatResponse(BaseModel):
    answer: str
    model: str
    conversation_id: str
    # 命中的参考资料（content/document_id/filename/kb_id/score），未启用 RAG 时为空
    sources: list[dict] = Field(default_factory=list)

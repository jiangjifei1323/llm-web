"""问答模块的请求/响应模型。"""

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant|system)$")
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=40)
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    answer: str
    model: str
    conversation_id: str

"""Agent 任务相关请求/响应模型。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentTaskCreateRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None
    kb_ids: list[int] = Field(default_factory=list, max_length=10)
    mode: Literal["auto", "agent", "chat"] = "auto"


class AgentTaskCreateResponse(BaseModel):
    task_id: str | None = None
    conversation_id: str
    status: str | None = None
    mode: Literal["chat", "agent"]


class AgentTaskView(BaseModel):
    id: str
    conversation_id: str
    user_id: int
    input: str
    status: str
    mode: str
    kb_ids: list[int] = Field(default_factory=list)
    plan: list[dict[str, Any]] | None = None
    used_steps: int = 0
    used_llm_calls: int = 0
    used_tool_calls: int = 0
    used_tokens: int = 0
    deadline_at: datetime | None = None
    error: str | None = None
    result_message_id: int | None = None
    cancel_requested: bool = False
    retry_count: int = 0
    replan_count: int = 0
    pending_approval: dict[str, Any] | None = None
    has_checkpoint: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AgentCancelResponse(BaseModel):
    id: str
    status: str
    cancel_requested: bool


class AgentApprovalRequest(BaseModel):
    action: Literal["approve", "reject", "edit"]
    # edit 时覆盖工具参数；approve 可省略
    args: dict[str, Any] | None = None
    reason: str | None = Field(default=None, max_length=500)


class AgentApprovalResponse(BaseModel):
    id: str
    status: str
    action: str

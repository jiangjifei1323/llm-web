"""问答助手后端入口。"""

import json
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import auth, db
from .db import User
from .llm import chat_completion, chat_completion_stream, get_model_name

# 加载 backend/.env（LLM_API_KEY、DATABASE_URL 等配置）
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    auth.seed_admin()  # 确保管理员账户 admin/admin 存在
    yield


app = FastAPI(title="llm-web 问答助手", version="0.1.0", lifespan=lifespan)

# 开发阶段放开跨域，方便前端直连调试（走 Vite 代理时并不依赖此项）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant|system)$")
    content: str


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


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=40)
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    answer: str
    model: str
    conversation_id: str


@app.get("/api/health")
def health() -> dict[str, str]:
    try:
        db.ping()
        database = "ok"
    except Exception as exc:  # noqa: BLE001 - 健康检查需吞掉连接异常
        database = f"error: {exc}"
    return {"status": "ok", "database": database}


# ---------- 用户认证 ----------


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


def _token_response(user: User) -> TokenResponse:
    return TokenResponse(
        access_token=auth.create_token(user), username=user.username, is_admin=user.is_admin
    )


@app.post("/api/auth/register", response_model=TokenResponse)
def register(req: RegisterRequest) -> TokenResponse:
    """注册普通用户，成功后直接返回令牌（自动登录）。"""
    if db.get_user_by_username(req.username) is not None:
        raise HTTPException(status_code=409, detail="用户名已存在")
    user = db.create_user(req.username, auth.hash_password(req.password))
    return _token_response(user)


@app.post("/api/auth/login", response_model=TokenResponse)
def login(req: LoginRequest) -> TokenResponse:
    user = db.get_user_by_username(req.username)
    if user is None or not auth.verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return _token_response(user)


@app.get("/api/auth/me", response_model=UserInfo)
def me(user: User = Depends(auth.get_current_user)) -> UserInfo:
    return UserInfo(username=user.username, is_admin=user.is_admin)


# ---------- 问答 ----------


def _resolve_conversation_id(req: ChatRequest) -> str:
    return req.conversation_id or db.new_conversation_id()


def _build_messages(req: ChatRequest, conversation_id: str) -> list[dict[str, str]]:
    """组装 system 指令 + 历史对话 + 当前问题。

    前端没带 history 但带了已存在的 conversation_id 时，从数据库补历史。
    """
    messages = [{"role": "system", "content": "你是一名乐于助人的问答助手，请用简体中文回答问题。"}]
    history = [m.model_dump() for m in req.history]
    if not history and req.conversation_id:
        history = db.load_conversation_messages(conversation_id)[-40:]
    messages.extend(history)
    messages.append({"role": "user", "content": req.message})
    return messages


def _check_conversation_access(conversation_id: str, user: User) -> None:
    """校验会话归属：他人会话返回 403；无主的历史会话归属给当前用户。"""
    conv = db.get_conversation(conversation_id)
    if conv is None:
        return  # 新会话，落库时会写入归属
    if conv.user_id is None:
        db.claim_conversation(conversation_id, user.id)
        return
    if conv.user_id != user.id and not user.is_admin:
        raise HTTPException(status_code=403, detail="无权访问该会话")


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest, user: User = Depends(auth.get_current_user)) -> ChatResponse:
    conversation_id = _resolve_conversation_id(req)
    _check_conversation_access(conversation_id, user)
    answer, model = chat_completion(_build_messages(req, conversation_id))
    db.save_chat_round(conversation_id, req.message, answer, model, user_id=user.id)
    return ChatResponse(answer=answer, model=model, conversation_id=conversation_id)


@app.get("/api/conversations", response_model=list[ConversationSummary])
def list_conversations(user: User = Depends(auth.get_current_user)) -> list[ConversationSummary]:
    """当前用户的会话列表（供前端左侧栏展示），按最近更新倒序。"""
    return [ConversationSummary(**item) for item in db.list_user_conversations(user.id)]


@app.patch("/api/conversations/{conversation_id}", response_model=RenamedConversation)
def rename_conversation(
    conversation_id: str,
    req: RenameRequest,
    user: User = Depends(auth.get_current_user),
) -> RenamedConversation:
    """修改会话名称：仅限本人会话（管理员可改任意会话）。"""
    if db.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    _check_conversation_access(conversation_id, user)
    title = req.title.strip()[: db.TITLE_MAX_LEN]
    if not title:
        raise HTTPException(status_code=422, detail="会话名称不能为空")
    db.rename_conversation(conversation_id, title)
    return RenamedConversation(id=conversation_id, title=title)


@app.delete("/api/conversations/{conversation_id}", response_model=DeletedConversation)
def delete_conversation(
    conversation_id: str, user: User = Depends(auth.get_current_user)
) -> DeletedConversation:
    """软删除会话（只置 `deleted_at`，记录与消息保留在库内）：仅限本人会话，管理员可删任意会话。"""
    # 归属校验：会话不存在或已是删除态时放行，由下面的删除结果返回 404
    _check_conversation_access(conversation_id, user)
    if not db.delete_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    return DeletedConversation(id=conversation_id, deleted=True)


@app.get("/api/conversations/{conversation_id}/messages")
def conversation_messages(
    conversation_id: str, user: User = Depends(auth.get_current_user)
) -> list[ChatMessage]:
    """查询某个会话的完整历史消息。"""
    _check_conversation_access(conversation_id, user)
    messages = db.load_conversation_messages(conversation_id)
    if not messages:
        raise HTTPException(status_code=404, detail="会话不存在或暂无消息")
    return [ChatMessage(**m) for m in messages]


def _sse(payload: dict) -> str:
    """序列化为一条 SSE 消息。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/api/chat/stream")
def chat_stream(req: ChatRequest, user: User = Depends(auth.get_current_user)) -> StreamingResponse:
    """SSE 流式问答：逐块推送 data: {"delta": ...}，结束推送 [DONE]。

    完整回答累积后与用户提问一起落库。
    """
    conversation_id = _resolve_conversation_id(req)
    _check_conversation_access(conversation_id, user)
    messages = _build_messages(req, conversation_id)

    def event_gen() -> Iterator[str]:
        # 首条消息携带模型名与会话 id，供前端展示/延续会话
        yield _sse({"delta": "", "model": get_model_name(), "conversation_id": conversation_id})
        parts: list[str] = []
        try:
            for delta in chat_completion_stream(messages):
                parts.append(delta)
                yield _sse({"delta": delta})
            db.save_chat_round(
                conversation_id, req.message, "".join(parts), get_model_name(), user_id=user.id
            )
            yield "data: [DONE]\n\n"
        except RuntimeError as exc:
            # 流已开始、无法再改状态码，故以 error 事件下发
            yield _sse({"error": str(exc)})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------- 后台：用户管理（仅管理员） ----------


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


def _user_row(user: User) -> AdminUser:
    return AdminUser(
        id=user.id, username=user.username, is_admin=user.is_admin, created_at=user.created_at
    )


@app.get("/api/admin/users", response_model=list[AdminUser])
def admin_list_users(_: User = Depends(auth.require_admin)) -> list[AdminUser]:
    """系统所有用户（含管理员），按 id 正序。"""
    return [AdminUser(**item) for item in db.list_users()]


@app.post("/api/admin/users", response_model=AdminUser, status_code=201)
def admin_create_user(req: AdminUserCreate, _: User = Depends(auth.require_admin)) -> AdminUser:
    """新建用户，可直接指定管理员身份（无需自动登录）。"""
    if db.is_username_taken(req.username):
        raise HTTPException(status_code=409, detail="用户名已存在")
    user = db.create_user(req.username, auth.hash_password(req.password), is_admin=req.is_admin)
    return _user_row(user)


@app.patch("/api/admin/users/{user_id}", response_model=AdminUser)
def admin_update_user(
    user_id: int, req: AdminUserUpdate, _: User = Depends(auth.require_admin)
) -> AdminUser:
    """修改用户名 / 重置密码 / 切换管理员身份。"""
    target = db.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if req.username is None and req.password is None and req.is_admin is None:
        raise HTTPException(status_code=422, detail="没有需要修改的字段")
    if req.username is not None and req.username != target.username:
        if db.is_username_taken(req.username, exclude_id=user_id):
            raise HTTPException(status_code=409, detail="用户名已存在")
    if req.is_admin is False and target.is_admin and db.count_admins() <= 1:
        raise HTTPException(status_code=409, detail="系统至少需要保留一个管理员")
    db.update_user(
        user_id,
        username=req.username,
        password_hash=auth.hash_password(req.password) if req.password else None,
        is_admin=req.is_admin,
    )
    updated = db.get_user_by_id(user_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return _user_row(updated)


@app.delete("/api/admin/users/{user_id}", response_model=DeletedUser)
def admin_delete_user(
    user_id: int, operator: User = Depends(auth.require_admin)
) -> DeletedUser:
    """删除用户（其会话一并软删除，数据保留在库内）。"""
    target = db.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user_id == operator.id:
        raise HTTPException(status_code=409, detail="不能删除当前登录的账号")
    if target.is_admin and db.count_admins() <= 1:
        raise HTTPException(status_code=409, detail="系统至少需要保留一个管理员")
    if not db.delete_user(user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    return DeletedUser(id=user_id, deleted=True)

"""会话接口：列表、改名、删除与历史消息查询。"""

from fastapi import APIRouter, Depends, HTTPException

from .. import auth, db
from ..db import User
from ..schemas.chat import ChatMessage
from ..schemas.conversation import (
    ConversationSummary,
    DeletedConversation,
    RenameRequest,
    RenamedConversation,
)
from .deps import check_conversation_access

router = APIRouter(prefix="/api/conversations", tags=["会话"])


@router.get("", response_model=list[ConversationSummary])
def list_conversations(user: User = Depends(auth.get_current_user)) -> list[ConversationSummary]:
    """当前用户的会话列表（供前端左侧栏展示），按最近更新倒序。"""
    return [ConversationSummary(**item) for item in db.list_user_conversations(user.id)]


@router.patch("/{conversation_id}", response_model=RenamedConversation)
def rename_conversation(
    conversation_id: str,
    req: RenameRequest,
    user: User = Depends(auth.get_current_user),
) -> RenamedConversation:
    """修改会话名称：仅限本人会话（管理员可改任意会话）。"""
    if db.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    check_conversation_access(conversation_id, user)
    title = req.title.strip()[: db.TITLE_MAX_LEN]
    if not title:
        raise HTTPException(status_code=422, detail="会话名称不能为空")
    db.rename_conversation(conversation_id, title)
    return RenamedConversation(id=conversation_id, title=title)


@router.delete("/{conversation_id}", response_model=DeletedConversation)
def delete_conversation(
    conversation_id: str, user: User = Depends(auth.get_current_user)
) -> DeletedConversation:
    """软删除会话（只置 `deleted_at`，记录与消息保留在库内）：仅限本人会话，管理员可删任意会话。"""
    # 归属校验：会话不存在或已是删除态时放行，由下面的删除结果返回 404
    check_conversation_access(conversation_id, user)
    if not db.delete_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    return DeletedConversation(id=conversation_id, deleted=True)


@router.get("/{conversation_id}/messages")
def conversation_messages(
    conversation_id: str, user: User = Depends(auth.get_current_user)
) -> list[ChatMessage]:
    """查询某个会话的完整历史消息。"""
    check_conversation_access(conversation_id, user)
    messages = db.load_conversation_messages(conversation_id)
    if not messages:
        raise HTTPException(status_code=404, detail="会话不存在或暂无消息")
    return [ChatMessage(**m) for m in messages]

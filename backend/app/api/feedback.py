"""消息反馈接口：用户对回答进行评价。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import auth, db
from ..db import User

router = APIRouter(prefix="/api/messages", tags=["反馈"])


class FeedbackRequest(BaseModel):
    """反馈请求：rating=1 表示踩，rating=2 表示赞。"""

    rating: int = Field(..., ge=1, le=2, description="1=踩, 2=赞")


class FeedbackResponse(BaseModel):
    message_id: int
    rating: int


@router.post("/{message_id}/feedback", response_model=FeedbackResponse)
def submit_feedback(
    message_id: int,
    req: FeedbackRequest,
    user: User = Depends(auth.get_current_user),
) -> FeedbackResponse:
    """提交消息反馈。仅限本人会话内的消息。"""
    # 验证消息存在且属于当前用户
    with db.SessionLocal() as session:
        msg = session.get(db.Message, message_id)
        if msg is None:
            raise HTTPException(status_code=404, detail="消息不存在")
        conv = session.get(db.Conversation, msg.conversation_id)
        if conv is None or conv.user_id != user.id:
            if not user.is_admin:
                raise HTTPException(status_code=403, detail="无权访问该消息")

    if not db.set_message_rating(message_id, req.rating):
        raise HTTPException(status_code=404, detail="消息不存在")

    return FeedbackResponse(message_id=message_id, rating=req.rating)

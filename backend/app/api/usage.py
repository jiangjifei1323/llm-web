"""Token 用量统计接口。"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from .. import auth, db
from ..db import User
from ..db.models import LLMCall

router = APIRouter(prefix="/api/conversations", tags=["用量"])


@router.get("/{conversation_id}/usage")
def get_conversation_usage(
    conversation_id: str, user: User = Depends(auth.get_current_user)
) -> dict:
    """获取指定会话的 token 用量统计。"""
    # 验证会话归属
    conv = db.get_conversation(conversation_id)
    if conv is None:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "call_count": 0}

    if conv.user_id != user.id and not user.is_admin:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "call_count": 0}

    # 从 llm_calls 表聚合查询
    with db.SessionLocal() as session:
        result = session.execute(
            select(
                func.coalesce(func.sum(LLMCall.input_tokens), 0),
                func.coalesce(func.sum(LLMCall.output_tokens), 0),
                func.coalesce(func.sum(LLMCall.total_tokens), 0),
                func.count(LLMCall.id),
            ).where(
                LLMCall.conversation_id == conversation_id,
                LLMCall.success.is_(True),
            )
        ).one()

    return {
        "input_tokens": result[0],
        "output_tokens": result[1],
        "total_tokens": result[2],
        "call_count": result[3],
    }

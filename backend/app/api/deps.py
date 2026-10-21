"""接口层共享守卫：跨模块复用的权限校验逻辑。"""

from fastapi import HTTPException

from .. import db
from ..db import User


def check_conversation_access(conversation_id: str, user: User) -> None:
    """校验会话归属：他人会话返回 403；无主的历史会话归属给当前用户。"""
    conv = db.get_conversation(conversation_id)
    if conv is None:
        return  # 新会话，落库时会写入归属
    if conv.user_id is None:
        db.claim_conversation(conversation_id, user.id)
        return
    if conv.user_id != user.id and not user.is_admin:
        raise HTTPException(status_code=403, detail="无权访问该会话")

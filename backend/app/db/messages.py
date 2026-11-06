"""消息表的数据访问（反馈等）。"""

from .base import SessionLocal
from .models import Message


def set_message_rating(message_id: int, rating: int) -> bool:
    """设置消息反馈评分：1=踩, 2=赞。返回是否成功。"""
    with SessionLocal() as session:
        msg = session.get(Message, message_id)
        if msg is None:
            return False
        msg.rating = rating
        session.commit()
        return True


def get_message_rating(message_id: int) -> int | None:
    """获取消息的反馈评分。"""
    with SessionLocal() as session:
        msg = session.get(Message, message_id)
        if msg is None:
            return None
        return msg.rating

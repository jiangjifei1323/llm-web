"""ORM 模型：用户、会话与问答消息。"""

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import DateTimeSec, Base, _now

# 会话名称列宽：超长的问题文本会截断到这里
TITLE_MAX_LEN = 200


class User(Base):
    """应用用户（含管理员）。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTimeSec, default=_now)


class Conversation(Base):
    """一次对话会话。"""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 会话名称：首轮问答时取用户第一个问题，之后可由用户改名
    title: Mapped[str | None] = mapped_column(String(TITLE_MAX_LEN), nullable=True)
    # 软删除标记：为空表示未删除；置为删除时间后对所有读接口不可见
    deleted_at: Mapped[datetime | None] = mapped_column(DateTimeSec, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTimeSec, default=_now)


class Message(Base):
    """一轮问答：用户问题与模型回答存同一行。"""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTimeSec, default=_now)

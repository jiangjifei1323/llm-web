"""ORM 模型：用户、会话、问答消息与知识库。"""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import DateTimeSec, Base, _now

# 会话名称列宽：超长的问题文本会截断到这里
TITLE_MAX_LEN = 200

# Embedding 向量维度（当前模型 qwen3.x-text-embedding-flash 实测返回 1024）
EMBEDDING_DIM = 1024


class User(Base):
    """应用用户（含管理员）。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # 软删除标记：为空表示未删除；置为删除时间后对所有读取入口不可见（用户名仍被占用）
    deleted_at: Mapped[datetime | None] = mapped_column(DateTimeSec, nullable=True)
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
    # 用户反馈：1=踩, 2=赞；NULL 表示未评价
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 本轮问答的 token 用量冗余（来自 llm_calls），NULL 表示尚未记录（如中断的轮次）
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 本轮命中的参考资料（JSON 数组字符串，元素为 content/document_id/filename/kb_id/score）
    # NULL 表示未启用 RAG；"[]" 表示启用但无命中
    sources: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTimeSec, default=_now)


class LLMCall(Base):
    """模型调用日志：记录每次问答发送给模型的输入、返回的输出与 token 用量。"""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    model: Mapped[str] = mapped_column(String(64))
    # 发送给模型的完整消息序列（system + 历史 + 当前问题），以 JSON 字符串存储
    input: Mapped[str] = mapped_column(Text)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    # 统计接口全部按 created_at 过滤/分组，必须有索引
    created_at: Mapped[datetime] = mapped_column(DateTimeSec, default=_now, index=True)


class KnowledgeBase(Base):
    """知识库：按用户隔离，每个用户只能看到并操作自己创建的库。"""

    __tablename__ = "knowledge_bases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 软删除标记：与 conversations 一致的语义
    deleted_at: Mapped[datetime | None] = mapped_column(DateTimeSec, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTimeSec, default=_now)


class KBDocument(Base):
    """知识库中的一份原始文档及其入库状态。"""

    __tablename__ = "kb_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    # 归一化类型：txt / md / pdf / docx
    file_type: Mapped[str] = mapped_column(String(16))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    # pending -> embedding -> ready / failed
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTimeSec, default=_now)


class KBChunk(Base):
    """文档切块后的向量行：检索的最小单位。"""

    __tablename__ = "kb_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("kb_documents.id", ondelete="CASCADE"), index=True
    )
    kb_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    # 块在文档内的序号（从 0 开始），用于展示出处位置
    position: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))

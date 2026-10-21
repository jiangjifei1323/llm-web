"""PostgreSQL 数据库连接与会话/消息持久化（SQLAlchemy 2.x 同步引擎）。"""

import os
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    func,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP as PG_TIMESTAMP
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)

# 独立导入本模块时也保证 .env 已加载（与 main.py 加载同一文件，幂等）
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:123456@127.0.0.1:5432/llm_db",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# 会话名称列宽：超长的问题文本会截断到这里
TITLE_MAX_LEN = 200


def _now() -> datetime:
    # 无时区：存服务器本地时间，只保留年月日时分秒（截断微秒）
    return datetime.now().replace(microsecond=0)


# 时间列统一为无时区 + 秒精度：PostgreSQL 下为 timestamp(0) without time zone
DateTimeSec = DateTime().with_variant(PG_TIMESTAMP(precision=0), "postgresql")


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


def init_db() -> None:
    """开发阶段直接建表；后续表结构变更可引入 Alembic 迁移。"""
    Base.metadata.create_all(engine)
    _migrate_legacy_tables()
    _migrate_messages_table()
    # 需排在 messages 迁移之后：回填会话名依赖 question 列
    _migrate_conversations_title()
    _migrate_conversations_deleted_at()
    _migrate_timestamp_columns()


def _migrate_legacy_tables() -> None:
    """轻量迁移：create_all 不会给已存在的表加列，这里手动补齐。"""
    insp = inspect(engine)
    columns = {c["name"] for c in insp.get_columns("conversations")}
    if "user_id" not in columns:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE conversations "
                    "ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE SET NULL"
                )
            )
            conn.execute(
                text("CREATE INDEX ix_conversations_user_id ON conversations (user_id)")
            )


def _migrate_conversations_title() -> None:
    """新增 conversations.title 列，并用每个会话的首个问题回填存量会话名。"""
    insp = inspect(engine)
    columns = {c["name"] for c in insp.get_columns("conversations")}
    if "title" in columns:
        return
    with engine.begin() as conn:
        conn.execute(
            text(f"ALTER TABLE conversations ADD COLUMN title VARCHAR({TITLE_MAX_LEN})")
        )
        if "messages" in insp.get_table_names():
            conn.execute(
                text(
                    "UPDATE conversations c "
                    "SET title = LEFT(BTRIM(m.question), :max_len) "
                    "FROM ( "
                    "  SELECT DISTINCT ON (conversation_id) conversation_id, question "
                    "  FROM messages ORDER BY conversation_id, id "
                    ") m "
                    "WHERE m.conversation_id = c.id AND c.title IS NULL"
                ),
                {"max_len": TITLE_MAX_LEN},
            )


def _migrate_conversations_deleted_at() -> None:
    """新增 conversations.deleted_at 列（软删除标记）：存量会话取 NULL，即视为未删除。"""
    insp = inspect(engine)
    columns = {c["name"] for c in insp.get_columns("conversations")}
    if "deleted_at" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE conversations ADD COLUMN deleted_at TIMESTAMP(0)"))


def _migrate_messages_table() -> None:
    """旧表（role/content 两行存一轮问答）重建为 question/answer 单行结构。"""
    insp = inspect(engine)
    if "messages" not in insp.get_table_names():
        return
    columns = {c["name"] for c in insp.get_columns("messages")}
    if "question" in columns and "role" not in columns:
        return  # 已是新结构
    with engine.begin() as conn:
        # 按会话内出现顺序，把 user 提问与其后的 assistant 回答配成一对
        conn.execute(
            text(
                "CREATE TABLE messages__new ("
                "  id SERIAL PRIMARY KEY,"
                "  conversation_id VARCHAR(36) NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,"
                "  question TEXT NOT NULL,"
                "  answer TEXT,"
                "  model VARCHAR(64),"
                "  created_at TIMESTAMP(0) NOT NULL DEFAULT CURRENT_TIMESTAMP"
                ")"
            )
        )
        conn.execute(
            text(
                "INSERT INTO messages__new (conversation_id, question, answer, model, created_at) "
                "SELECT conversation_id, "
                "  MAX(CASE WHEN role = 'user' THEN content END), "
                "  MAX(CASE WHEN role = 'assistant' THEN content END), "
                "  MAX(CASE WHEN role = 'assistant' THEN model END), "
                "  MIN(created_at) "
                "FROM ( "
                "  SELECT conversation_id, role, content, model, created_at, "
                "    FLOOR((ROW_NUMBER() OVER (PARTITION BY conversation_id ORDER BY id) - 1) / 2) AS round_no "
                "  FROM messages "
                "  WHERE role IN ('user', 'assistant') "
                ") paired "
                "GROUP BY conversation_id, round_no"
            )
        )
        conn.execute(text("DROP TABLE messages"))
        conn.execute(text("ALTER TABLE messages__new RENAME TO messages"))
        conn.execute(
            text("CREATE INDEX ix_messages_conversation_id ON messages (conversation_id)")
        )


def _migrate_timestamp_columns() -> None:
    """将已有表的 created_at 统一为 timestamp(0) 无时区（存北京时间墙上时间）。"""
    with engine.connect() as conn:
        tables = (
            conn.execute(
                text(
                    "SELECT table_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND column_name = 'created_at' "
                    "AND (data_type <> 'timestamp without time zone' "
                    "OR datetime_precision IS DISTINCT FROM 0)"
                )
            )
            .scalars()
            .all()
        )
    for table in tables:
        with engine.begin() as conn:
            # timestamptz -> 本地时区墙钟时间；naive 列则原样保留
            conn.execute(
                text(
                    f"ALTER TABLE {table} "  # noqa: S608 - 表名来自 information_schema
                    "ALTER COLUMN created_at TYPE timestamp(0) "
                    "USING CASE WHEN pg_typeof(created_at) = 'timestamp with time zone'::regtype "
                    "THEN created_at AT TIME ZONE 'Asia/Shanghai' ELSE created_at END"
                )
            )


def ping() -> None:
    """健康检查用：执行一次最简单的查询，失败时抛异常。"""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


def get_user_by_username(username: str) -> User | None:
    with SessionLocal() as session:
        return session.scalar(select(User).where(User.username == username))


def create_user(username: str, password_hash: str, is_admin: bool = False) -> User:
    with SessionLocal() as session:
        user = User(username=username, password_hash=password_hash, is_admin=is_admin)
        session.add(user)
        session.commit()
        return user


def list_users() -> list[dict[str, Any]]:
    """后台用户管理用：全部用户（含管理员），按 id 正序。"""
    with SessionLocal() as session:
        users = session.execute(select(User).order_by(User.id)).scalars().all()
        return [
            {
                "id": user.id,
                "username": user.username,
                "is_admin": user.is_admin,
                "created_at": user.created_at,
            }
            for user in users
        ]


def get_user_by_id(user_id: int) -> User | None:
    with SessionLocal() as session:
        return session.get(User, user_id)


def is_username_taken(username: str, exclude_id: int | None = None) -> bool:
    """用户名是否已被占用；改名时通过 exclude_id 排除自己。"""
    with SessionLocal() as session:
        stmt = select(User.id).where(User.username == username)
        if exclude_id is not None:
            stmt = stmt.where(User.id != exclude_id)
        return session.scalar(stmt) is not None


def count_admins() -> int:
    """管理员数量：用于兜住「至少保留一个管理员」的护栏。"""
    with SessionLocal() as session:
        return session.scalar(select(func.count(User.id)).where(User.is_admin.is_(True))) or 0


def update_user(
    user_id: int,
    *,
    username: str | None = None,
    password_hash: str | None = None,
    is_admin: bool | None = None,
) -> None:
    """按需更新用户名 / 密码 / 管理员标识，值为 None 表示不改这一项。"""
    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None:
            return
        if username is not None:
            user.username = username
        if password_hash is not None:
            user.password_hash = password_hash
        if is_admin is not None:
            user.is_admin = is_admin
        session.commit()


def delete_user(user_id: int) -> bool:
    """删除用户，同时软删除其名下未删除的会话。

    会话外键是 ON DELETE SET NULL，若不先打删除标记，被删用户的会话会变成
    无主会话，后续聊天时可能被 claim_conversation 归属给其他用户。
    """
    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None:
            return False
        session.execute(
            update(Conversation)
            .where(Conversation.user_id == user_id, Conversation.deleted_at.is_(None))
            .values(deleted_at=_now())
        )
        session.delete(user)
        session.commit()
        return True


def get_conversation(conversation_id: str) -> Conversation | None:
    """取会话；已软删除的一律视为不存在，调用方无需再判断删除态。"""
    with SessionLocal() as session:
        conv = session.get(Conversation, conversation_id)
        if conv is None or conv.deleted_at is not None:
            return None
        return conv


def claim_conversation(conversation_id: str, user_id: int) -> None:
    """将无主的历史会话（用户体系上线前产生）归属给指定用户。"""
    with SessionLocal() as session:
        conv = session.get(Conversation, conversation_id)
        if conv is not None and conv.user_id is None:
            conv.user_id = user_id
            session.commit()


def _title_from_question(question: str) -> str | None:
    """首轮问答时用用户问题作为会话名称：去空白并按列宽截断。"""
    trimmed = question.strip()[:TITLE_MAX_LEN]
    return trimmed or None


def save_chat_round(
    conversation_id: str,
    question: str,
    answer: str,
    model: str | None = None,
    user_id: int | None = None,
) -> None:
    """落库一轮问答（单行 question/answer）：会话不存在则先创建。

    会话名称只在首个问题写入；用户改过名（title 非空）则保持不变。
    命中已软删除的会话时撤销删除标记（复活该会话并继续累积问答）。
    """
    with SessionLocal() as session:
        conv = session.get(Conversation, conversation_id)
        if conv is None:
            session.add(
                Conversation(
                    id=conversation_id,
                    user_id=user_id,
                    title=_title_from_question(question),
                )
            )
        else:
            if conv.deleted_at is not None:
                conv.deleted_at = None
            if conv.title is None:
                conv.title = _title_from_question(question)
        session.add(
            Message(
                conversation_id=conversation_id,
                question=question,
                answer=answer,
                model=model,
            )
        )
        session.commit()


def rename_conversation(conversation_id: str, title: str) -> None:
    """修改会话名称（调用方需先校验会话存在与归属）。"""
    with SessionLocal() as session:
        conv = session.get(Conversation, conversation_id)
        if conv is None:
            return
        conv.title = title
        session.commit()


def delete_conversation(conversation_id: str) -> bool:
    """软删除会话：只把 deleted_at 置为当前时间，会话与其消息都留在库内。

    返回是否完成一次删除；会话不存在或已是删除态时返回 False。
    """
    with SessionLocal() as session:
        conv = session.get(Conversation, conversation_id)
        if conv is None or conv.deleted_at is not None:
            return False
        conv.deleted_at = _now()
        session.commit()
        return True


def load_conversation_messages(conversation_id: str) -> list[dict[str, str]]:
    """按时间顺序取会话内的问答，展开为 role/content 序列（供补历史时使用）。

    会话已软删除时不产出任何消息。
    """
    with SessionLocal() as session:
        rows = session.scalars(
            select(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.conversation_id == conversation_id,
                Conversation.deleted_at.is_(None),
            )
            .order_by(Message.id)
        ).all()
    result: list[dict[str, str]] = []
    for r in rows:
        result.append({"role": "user", "content": r.question})
        if r.answer is not None:
            result.append({"role": "assistant", "content": r.answer})
    return result


def list_user_conversations(user_id: int) -> list[dict[str, Any]]:
    """当前用户的会话摘要（只含未删除的）：名称取 conversations.title，按最近一次问答时间倒序。"""
    with SessionLocal() as session:
        convs = session.scalars(
            select(Conversation).where(
                Conversation.user_id == user_id,
                Conversation.deleted_at.is_(None),
            )
        ).all()
        if not convs:
            return []
        stats = session.execute(
            select(
                Message.conversation_id,
                func.max(Message.created_at).label("last_at"),
            )
            .where(Message.conversation_id.in_([c.id for c in convs]))
            .group_by(Message.conversation_id)
        ).all()

    updated = {cid: last_at for cid, last_at in stats}

    items = [
        {
            "id": conv.id,
            "title": conv.title or "新会话",
            "updated_at": updated.get(conv.id, conv.created_at),
        }
        for conv in convs
    ]
    items.sort(key=lambda item: item["updated_at"], reverse=True)
    return items


def new_conversation_id() -> str:
    return str(uuid4())

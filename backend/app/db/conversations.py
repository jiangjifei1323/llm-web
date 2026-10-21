"""会话与问答消息的数据访问。"""

from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from .base import SessionLocal, _now
from .models import Conversation, Message, TITLE_MAX_LEN


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

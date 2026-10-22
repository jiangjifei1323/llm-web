"""数据层。按职责拆分为 base / models / migrations / users / conversations，

对外统一从这里导出，调用方仍可用 `from . import db` + `db.xxx()` 的方式访问。
"""

from .base import DATABASE_URL, Base, DateTimeSec, SessionLocal, engine, ping
from .conversations import (
    claim_conversation,
    delete_conversation,
    get_conversation,
    list_user_conversations,
    load_conversation_messages,
    log_llm_call,
    new_conversation_id,
    rename_conversation,
    save_chat_round,
)
from .migrations import init_db
from .models import TITLE_MAX_LEN, Conversation, LLMCall, Message, User
from .users import (
    count_admins,
    create_user,
    delete_user,
    get_user_by_id,
    get_user_by_username,
    is_username_taken,
    list_users,
    reactivate_user_by_username,
    update_user,
)

__all__ = [
    "DATABASE_URL",
    "Base",
    "DateTimeSec",
    "SessionLocal",
    "engine",
    "ping",
    "init_db",
    "TITLE_MAX_LEN",
    "User",
    "Conversation",
    "Message",
    "LLMCall",
    "get_user_by_username",
    "create_user",
    "list_users",
    "get_user_by_id",
    "is_username_taken",
    "count_admins",
    "update_user",
    "delete_user",
    "reactivate_user_by_username",
    "get_conversation",
    "claim_conversation",
    "save_chat_round",
    "log_llm_call",
    "rename_conversation",
    "delete_conversation",
    "load_conversation_messages",
    "list_user_conversations",
    "new_conversation_id",
]

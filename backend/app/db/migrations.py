"""建表与存量库的轻量列迁移（开发期用，后续可换 Alembic）。"""

from sqlalchemy import inspect, text

from .base import engine
from .models import TITLE_MAX_LEN, Base


def init_db() -> None:
    """开发阶段直接建表；后续表结构变更可引入 Alembic 迁移。"""
    _ensure_vector_extension()  # 必须先于 create_all：kb_chunks 的列类型依赖 vector
    Base.metadata.create_all(engine)
    _migrate_legacy_tables()
    _migrate_messages_table()
    # 需排在 messages 迁移之后：回填会话名依赖 question 列
    _migrate_conversations_title()
    _migrate_conversations_deleted_at()
    _migrate_users_deleted_at()
    _migrate_messages_rating()
    _migrate_messages_tokens()
    _migrate_messages_sources()
    _migrate_llm_calls_created_at_index()
    _migrate_kb_vector_index()
    _migrate_kb_documents_deleted_at()
    _migrate_timestamp_columns()


def _ensure_vector_extension() -> None:
    """启用 pgvector 扩展：知识库向量列（vector 类型）依赖它。"""
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))


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


def _migrate_users_deleted_at() -> None:
    """新增 users.deleted_at 列（软删除标记）：存量用户取 NULL，即视为未删除。"""
    insp = inspect(engine)
    columns = {c["name"] for c in insp.get_columns("users")}
    if "deleted_at" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN deleted_at TIMESTAMP(0)"))


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


def _migrate_kb_documents_deleted_at() -> None:
    """新增 kb_documents.deleted_at 列（软删除标记）：存量文档取 NULL，即视为未删除。"""
    insp = inspect(engine)
    if "kb_documents" not in insp.get_table_names():
        return
    columns = {c["name"] for c in insp.get_columns("kb_documents")}
    if "deleted_at" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE kb_documents ADD COLUMN deleted_at TIMESTAMP(0)"))


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


def _migrate_messages_rating() -> None:
    """新增 messages.rating 列（用户反馈：1=踩, 2=赞）。"""
    insp = inspect(engine)
    if "messages" not in insp.get_table_names():
        return
    columns = {c["name"] for c in insp.get_columns("messages")}
    if "rating" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE messages ADD COLUMN rating INTEGER"))


def _migrate_messages_tokens() -> None:
    """新增 messages 的 token 冗余列，并用 llm_calls 回填历史消息的用量。

    历史消息此前靠读接口按 answer 文本反查 llm_calls，这里一次性回填，
    避免列上线后旧会话的用量显示消失。
    """
    insp = inspect(engine)
    if "messages" not in insp.get_table_names():
        return
    columns = {c["name"] for c in insp.get_columns("messages")}
    missing = [c for c in ("input_tokens", "output_tokens", "total_tokens") if c not in columns]
    if not missing:
        return
    with engine.begin() as conn:
        for column in missing:
            conn.execute(text(f"ALTER TABLE messages ADD COLUMN {column} INTEGER"))
        if "llm_calls" not in insp.get_table_names():
            return
        conn.execute(
            text(
                "UPDATE messages m "
                "SET input_tokens = c.input_tokens, "
                "    output_tokens = c.output_tokens, "
                "    total_tokens = c.total_tokens "
                "FROM ( "
                "  SELECT DISTINCT ON (conversation_id, output) "
                "    conversation_id, output, input_tokens, output_tokens, total_tokens "
                "  FROM llm_calls "
                "  WHERE success AND output IS NOT NULL "
                "  ORDER BY conversation_id, output, id DESC "
                ") c "
                "WHERE c.conversation_id = m.conversation_id AND c.output = m.answer "
                "AND m.total_tokens IS NULL"
            )
        )


def _migrate_messages_sources() -> None:
    """新增 messages.sources 列：存本轮命中的参考资料 JSON，历史会话据此回显。"""
    insp = inspect(engine)
    if "messages" not in insp.get_table_names():
        return
    columns = {c["name"] for c in insp.get_columns("messages")}
    if "sources" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE messages ADD COLUMN sources TEXT"))


def _migrate_llm_calls_created_at_index() -> None:
    """为 llm_calls.created_at 建索引：统计接口全部按该列过滤与分组。"""
    insp = inspect(engine)
    if "llm_calls" not in insp.get_table_names():
        return
    with engine.begin() as conn:
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_llm_calls_created_at ON llm_calls (created_at)")
        )


def _migrate_kb_vector_index() -> None:
    """为 kb_chunks.embedding 建 HNSW 余弦索引：向量检索只走相似度排序。

    create_all 无法表达 USING hnsw 的索引类型，这里幂等补齐。
    """
    insp = inspect(engine)
    if "kb_chunks" not in insp.get_table_names():
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_kb_chunks_embedding "
                "ON kb_chunks USING hnsw (embedding vector_cosine_ops)"
            )
        )

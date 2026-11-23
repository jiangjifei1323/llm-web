"""建表：开发阶段不做存量列迁移。

表结构以 [models.py](models.py) 为唯一事实来源：部署到测试/生产时按全量字段
直接建表即可，增删字段不必再在此追加幂等 ALTER，本地开发库删表重建就行。
这里只保留 `create_all` 表达不了的两件事：启用 pgvector 扩展、建 HNSW 向量索引。
"""

from sqlalchemy import text

from .base import engine
from .models import Base


def init_db() -> None:
    """按 models 全量建表，并补齐 create_all 无法声明的索引。"""
    _ensure_vector_extension()  # 必须先于 create_all：kb_chunks 的列类型依赖 vector
    Base.metadata.create_all(engine)
    _ensure_kb_vector_index()
    _ensure_llm_calls_task_id()
    _ensure_agent_task_m1_columns()


def _ensure_llm_calls_task_id() -> None:
    """存量库补齐 llm_calls.task_id（create_all 不会 ALTER 已有表）。"""
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE llm_calls "
                "ADD COLUMN IF NOT EXISTS task_id VARCHAR(36)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_llm_calls_task_id "
                "ON llm_calls (task_id)"
            )
        )


def _ensure_agent_task_m1_columns() -> None:
    """存量库补齐 agent_tasks 的 M1/M2 列。"""
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE agent_tasks "
                "ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE agent_tasks "
                "ADD COLUMN IF NOT EXISTS replan_count INTEGER DEFAULT 0"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE agent_tasks "
                "ADD COLUMN IF NOT EXISTS pending_approval_json TEXT"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE agent_tasks "
                "ADD COLUMN IF NOT EXISTS checkpoint_json TEXT"
            )
        )


def _ensure_vector_extension() -> None:
    """启用 pgvector 扩展：知识库向量列（vector 类型）依赖它。"""
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))


def _ensure_kb_vector_index() -> None:
    """为 kb_chunks.embedding 建 HNSW 余弦索引：create_all 无法表达 USING hnsw。"""
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_kb_chunks_embedding "
                "ON kb_chunks USING hnsw (embedding vector_cosine_ops)"
            )
        )

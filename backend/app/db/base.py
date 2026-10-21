"""数据库引擎、会话工厂与连接级操作（PostgreSQL / SQLAlchemy 2.x 同步引擎）。"""

import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import DateTime, create_engine, text
from sqlalchemy.dialects.postgresql import TIMESTAMP as PG_TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# 独立导入本模块时也保证 .env 已加载（与 main.py 加载同一文件，幂等）
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:123456@127.0.0.1:5432/llm_db",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# 时间列统一为无时区 + 秒精度：PostgreSQL 下为 timestamp(0) without time zone
DateTimeSec = DateTime().with_variant(PG_TIMESTAMP(precision=0), "postgresql")


def _now() -> datetime:
    # 无时区：存服务器本地时间，只保留年月日时分秒（截断微秒）
    return datetime.now().replace(microsecond=0)


def ping() -> None:
    """健康检查用：执行一次最简单的查询，失败时抛异常。"""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))

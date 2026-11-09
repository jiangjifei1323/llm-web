"""知识库、文档与向量块的数据访问。

知识库按用户隔离（软删除语义与会话一致）；文档记录入库状态，
块行只随文档级联删除，不提供单块编辑。
"""

from typing import Any

from sqlalchemy import func, select

from .base import SessionLocal, _now
from .models import KBChunk, KBDocument, KnowledgeBase


def create_knowledge_base(user_id: int, name: str, description: str | None = None) -> int:
    with SessionLocal() as session:
        kb = KnowledgeBase(user_id=user_id, name=name, description=description)
        session.add(kb)
        session.commit()
        session.refresh(kb)
        return kb.id


def get_knowledge_base(kb_id: int) -> KnowledgeBase | None:
    """取知识库；已软删除的一律视为不存在。"""
    with SessionLocal() as session:
        kb = session.get(KnowledgeBase, kb_id)
        if kb is None or kb.deleted_at is not None:
            return None
        return kb


def list_knowledge_bases(user_id: int, keyword: str = "") -> list[dict[str, Any]]:
    """当前用户的知识库列表（含文档数统计），按创建时间倒序。"""
    with SessionLocal() as session:
        stmt = select(KnowledgeBase).where(
            KnowledgeBase.user_id == user_id,
            KnowledgeBase.deleted_at.is_(None),
        )
        if keyword.strip():
            stmt = stmt.where(KnowledgeBase.name.ilike(f"%{keyword.strip()}%"))
        kbs = session.scalars(stmt.order_by(KnowledgeBase.id.desc())).all()
        if not kbs:
            return []
        counts = dict(
            session.execute(
                select(KBDocument.kb_id, func.count())
                .where(KBDocument.kb_id.in_([kb.id for kb in kbs]))
                .group_by(KBDocument.kb_id)
            ).all()
        )
    return [
        {
            "id": kb.id,
            "name": kb.name,
            "description": kb.description,
            "doc_count": counts.get(kb.id, 0),
            "created_at": kb.created_at,
        }
        for kb in kbs
    ]


def rename_knowledge_base(kb_id: int, name: str, description: str | None) -> None:
    with SessionLocal() as session:
        kb = session.get(KnowledgeBase, kb_id)
        if kb is not None:
            kb.name = name
            kb.description = description
            session.commit()


def delete_knowledge_base(kb_id: int) -> bool:
    """软删除知识库：文档与块保留在库内，检索接口按删除标记过滤。"""
    with SessionLocal() as session:
        kb = session.get(KnowledgeBase, kb_id)
        if kb is None or kb.deleted_at is not None:
            return False
        kb.deleted_at = _now()
        session.commit()
        return True


def add_document(
    kb_id: int, filename: str, file_type: str, size_bytes: int
) -> int:
    with SessionLocal() as session:
        doc = KBDocument(
            kb_id=kb_id,
            filename=filename,
            file_type=file_type,
            size_bytes=size_bytes,
            status="pending",
        )
        session.add(doc)
        session.commit()
        session.refresh(doc)
        return doc.id


def set_document_status(
    doc_id: int,
    status: str,
    error: str | None = None,
    chunk_count: int | None = None,
) -> None:
    with SessionLocal() as session:
        doc = session.get(KBDocument, doc_id)
        if doc is None:
            return
        doc.status = status
        doc.error = error
        if chunk_count is not None:
            doc.chunk_count = chunk_count
        session.commit()


def get_document(doc_id: int) -> dict[str, Any] | None:
    with SessionLocal() as session:
        doc = session.get(KBDocument, doc_id)
        if doc is None:
            return None
        return _doc_dict(doc)


def list_kb_documents(kb_id: int) -> list[dict[str, Any]]:
    with SessionLocal() as session:
        docs = session.scalars(
            select(KBDocument)
            .where(KBDocument.kb_id == kb_id)
            .order_by(KBDocument.id.desc())
        ).all()
        return [_doc_dict(doc) for doc in docs]


def delete_document(doc_id: int) -> bool:
    """硬删除文档：向量块由外键 CASCADE 一并清除。返回是否删掉一行。"""
    with SessionLocal() as session:
        doc = session.get(KBDocument, doc_id)
        if doc is None:
            return False
        session.delete(doc)
        session.commit()
        return True


def insert_chunks(
    document_id: int, kb_id: int, chunks: list[str], embeddings: list[list[float]]
) -> None:
    """批量写入向量块（一次事务），行数由调用方保证 chunks 与 embeddings 等长。"""
    with SessionLocal() as session:
        session.add_all(
            KBChunk(
                document_id=document_id,
                kb_id=kb_id,
                position=i,
                content=chunk,
                char_count=len(chunk),
                embedding=emb,
            )
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings))
        )
        session.commit()


def search_chunks(
    kb_id: int, vector: list[float], limit: int
) -> list[dict[str, Any]]:
    """在单个知识库内做余弦距离 Top-K 检索。

    只命中入库完成（status=ready）文档的块，且知识库须未被软删除。
    distance 为余弦距离（0 最相似），调用方可按 1-distance 折算相似度。
    """
    with SessionLocal() as session:
        distance = KBChunk.embedding.cosine_distance(vector)
        rows = session.execute(
            select(
                KBChunk.id,
                KBChunk.document_id,
                KBChunk.kb_id,
                KBChunk.position,
                KBChunk.content,
                KBDocument.filename,
                distance.label("distance"),
            )
            .join(KBDocument, KBDocument.id == KBChunk.document_id)
            .join(KnowledgeBase, KnowledgeBase.id == KBChunk.kb_id)
            .where(
                KBChunk.kb_id == kb_id,
                KBDocument.status == "ready",
                KnowledgeBase.deleted_at.is_(None),
            )
            .order_by(distance)
            .limit(limit)
        ).all()
    return [
        {
            "chunk_id": row.id,
            "document_id": row.document_id,
            "kb_id": row.kb_id,
            "position": row.position,
            "filename": row.filename,
            "content": row.content,
            "distance": float(row.distance),
        }
        for row in rows
    ]


def _doc_dict(doc: KBDocument) -> dict[str, Any]:
    return {
        "id": doc.id,
        "kb_id": doc.kb_id,
        "filename": doc.filename,
        "file_type": doc.file_type,
        "size_bytes": doc.size_bytes,
        "status": doc.status,
        "error": doc.error,
        "chunk_count": doc.chunk_count,
        "created_at": doc.created_at,
    }

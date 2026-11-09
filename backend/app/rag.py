"""RAG 检索：查询向量化 -> 多知识库向量召回 -> rerank 精排。

对调用方保证不抛异常：embedding 失败返回空结果（问答照常进行，只是没有参考资料），
rerank 未配置或调用失败时降级为按向量相似度排序。
"""

import logging

from . import db
from .llm import embed_text, rerank

logger = logging.getLogger(__name__)

# 每个知识库的向量召回候选数（送入 rerank 的池子大小）
_CANDIDATES_PER_KB = 8

# 最终注入提示词的参考资料条数
FINAL_TOP_K = 4


def _vector_search(query: str, kb_ids: list[int]) -> list[dict]:
    """跨库向量召回并合并，按余弦距离升序。"""
    vector = embed_text(query)
    candidates: list[dict] = []
    for kb_id in kb_ids:
        candidates.extend(db.search_chunks(kb_id, vector, _CANDIDATES_PER_KB))
    candidates.sort(key=lambda c: c["distance"])
    return candidates


def _to_source(chunk: dict, score: float) -> dict:
    return {
        "content": chunk["content"],
        "document_id": chunk["document_id"],
        "filename": chunk["filename"],
        "kb_id": chunk["kb_id"],
        "score": round(score, 4),
    }


def _rerank_scored(
    query: str, candidates: list[dict]
) -> list[tuple[dict, float]] | None:
    """rerank 精排；未配置或调用失败返回 None（调用方走相似度降级）。"""
    documents = [c["content"] for c in candidates]
    try:
        results = rerank(query, documents, top_n=FINAL_TOP_K)
    except Exception as exc:  # noqa: BLE001 - 精排失败不应阻断问答
        logger.warning("rerank 调用失败，降级为向量相似度排序：%s", exc)
        return None
    try:
        return [
            (candidates[int(r["index"])], float(r["relevance_score"]))
            for r in results
        ]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("rerank 返回格式异常，降级为向量相似度排序：%s", exc)
        return None


def retrieve(query: str, kb_ids: list[int]) -> list[dict]:
    """检索与 query 最相关的知识块，返回至多 FINAL_TOP_K 条 source。

    每项含 content / document_id / filename / kb_id / score。
    kb_ids 为空或检索失败时返回空列表。
    """
    if not kb_ids:
        return []
    try:
        candidates = _vector_search(query, kb_ids)
    except Exception as exc:  # noqa: BLE001 - embedding 失败时问答照常进行
        logger.warning("知识库检索失败，本轮不注入参考资料：%s", exc)
        return []
    if not candidates:
        return []

    if len(candidates) <= FINAL_TOP_K:
        return [_to_source(c, 1.0 - c["distance"]) for c in candidates]

    scored = _rerank_scored(query, candidates)
    if scored is None:
        scored = [(c, 1.0 - c["distance"]) for c in candidates[:FINAL_TOP_K]]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [_to_source(c, s) for c, s in scored[:FINAL_TOP_K]]

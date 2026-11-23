"""知识库相关工具：列表与检索（封装现有 rag / db）。"""

from __future__ import annotations

from typing import Any

from ... import db
from ...rag import retrieve
from .base import ToolContext, register


def _allowed_kb_ids(ctx: ToolContext, requested: list[int] | None) -> list[int]:
    """取模型请求与任务授权的交集；未指定则用任务携带的 kb_ids。"""
    base = list(ctx.kb_ids or [])
    if not requested:
        return base
    allowed = set(base)
    # 若任务未预选库，则按用户归属实时校验
    if not allowed:
        out: list[int] = []
        for kb_id in sorted(set(requested)):
            kb = db.get_knowledge_base(kb_id)
            if kb is None:
                continue
            if kb.user_id == ctx.user_id or ctx.is_admin:
                out.append(kb_id)
        return out
    return [i for i in sorted(set(requested)) if i in allowed]


@register(
    name="kb_list",
    description="列出当前用户可用的知识库（id、名称、文档数）。在不确定该搜哪个库时先调用。",
    input_schema={
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "按名称模糊过滤，可省略",
                "default": "",
            }
        },
        "required": [],
    },
    risk="read",
    timeout_seconds=10.0,
)
def kb_list(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    keyword = str(args.get("keyword") or "")
    rows = db.list_knowledge_bases(ctx.user_id, keyword=keyword)
    if ctx.kb_ids:
        allow = set(ctx.kb_ids)
        rows = [r for r in rows if r["id"] in allow]
    if not rows:
        return {
            "ok": True,
            "observation": "当前没有可用的知识库。",
            "data": {"items": []},
        }
    lines = [
        f"- id={r['id']} name={r['name']} docs={r['doc_count']}"
        + (f" desc={r['description']}" if r.get("description") else "")
        for r in rows
    ]
    return {
        "ok": True,
        "observation": "可用知识库：\n" + "\n".join(lines),
        "data": {
            "items": [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "doc_count": r["doc_count"],
                    "description": r.get("description"),
                }
                for r in rows
            ]
        },
    }


@register(
    name="kb_search",
    description=(
        "在授权的知识库中检索与问题相关的参考资料。"
        "可改写 query 多次检索；若首次结果不足请换关键词再搜。"
        "返回带编号的片段，最终回答应标注来源编号。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "检索用的查询语句，应具体、可检索",
            },
            "kb_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "要检索的知识库 id 列表；省略则使用任务预选的库",
            },
        },
        "required": ["query"],
    },
    risk="read",
    timeout_seconds=60.0,
)
def kb_search(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        return {
            "ok": False,
            "observation": "query 不能为空",
            "error": "invalid_args",
        }
    requested = args.get("kb_ids")
    if requested is not None and not isinstance(requested, list):
        return {
            "ok": False,
            "observation": "kb_ids 必须是整数数组",
            "error": "invalid_args",
        }
    kb_ids = _allowed_kb_ids(ctx, requested)
    if not kb_ids:
        return {
            "ok": False,
            "observation": "没有可检索的知识库。请先确认用户选择了知识库，或调用 kb_list。",
            "error": "no_kb",
        }
    sources = retrieve(query, kb_ids)
    if not sources:
        return {
            "ok": True,
            "observation": f"未检索到与「{query}」相关的资料（kb_ids={kb_ids}）。可改写 query 再试。",
            "data": {"sources": [], "kb_ids": kb_ids, "query": query},
        }
    lines = []
    for i, s in enumerate(sources, start=1):
        lines.append(
            f"[{i}]（kb={s.get('kb_id')} file={s.get('filename')} score={s.get('score')}）"
            f"{s.get('content', '')}"
        )
    return {
        "ok": True,
        "observation": "检索结果：\n" + "\n".join(lines),
        "data": {"sources": sources, "kb_ids": kb_ids, "query": query},
    }

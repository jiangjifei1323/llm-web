"""可选联网搜索工具（risk=external，默认需 HITL）。"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from .. import config
from .base import ToolContext, register


@register(
    name="web_search",
    description=(
        "在互联网上搜索公开信息。用于需要最新事实、新闻或站外资料时。"
        "返回若干条标题与摘要；请勿用于私密数据或攻击性探测。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词或问句",
            },
            "max_results": {
                "type": "integer",
                "description": "最多返回条数，默认 5",
                "default": 5,
            },
        },
        "required": ["query"],
    },
    risk="external",
    timeout_seconds=20.0,
    idempotent=True,
)
def web_search(_ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"ok": False, "observation": "query 不能为空", "error": "invalid_args"}
    max_results = int(args.get("max_results") or 5)
    max_results = max(1, min(max_results, 10))

    # 优先：若配置了专用 Base URL + Key，按简易 JSON API 约定请求
    # 约定响应：{"results":[{"title","url","snippet"}, ...]}
    if config.WEB_SEARCH_API_KEY and config.WEB_SEARCH_BASE_URL:
        return _search_custom(query, max_results)

    return _search_duckduckgo(query, max_results)


def _search_custom(query: str, max_results: int) -> dict[str, Any]:
    base = config.WEB_SEARCH_BASE_URL.rstrip("/")
    url = f"{base}?q={urllib.parse.quote(query)}&format=json"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {config.WEB_SEARCH_API_KEY}",
            "User-Agent": "llm-web-agent/0.1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "observation": f"联网搜索失败：{exc}",
            "error": "runtime",
        }
    items = raw.get("results") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return {
            "ok": False,
            "observation": "搜索接口返回格式异常",
            "error": "runtime",
        }
    return _format_results(items[:max_results], query)


def _search_duckduckgo(query: str, max_results: int) -> dict[str, Any]:
    """无 Key 回退：DuckDuckGo Instant Answer（结果有限但零依赖）。"""
    params = urllib.parse.urlencode(
        {"q": query, "format": "json", "no_html": 1, "skip_disambig": 1}
    )
    url = f"https://api.duckduckgo.com/?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "llm-web-agent/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "observation": f"DuckDuckGo 搜索失败：{exc}",
            "error": "runtime",
        }

    items: list[dict[str, Any]] = []
    abstract = (raw.get("AbstractText") or "").strip()
    if abstract:
        items.append(
            {
                "title": raw.get("Heading") or query,
                "url": raw.get("AbstractURL") or "",
                "snippet": abstract,
            }
        )
    for topic in raw.get("RelatedTopics") or []:
        if len(items) >= max_results:
            break
        if isinstance(topic, dict) and topic.get("Text"):
            items.append(
                {
                    "title": (topic.get("Text") or "")[:80],
                    "url": topic.get("FirstURL") or "",
                    "snippet": topic.get("Text") or "",
                }
            )
        elif isinstance(topic, dict) and isinstance(topic.get("Topics"), list):
            for sub in topic["Topics"]:
                if len(items) >= max_results:
                    break
                if isinstance(sub, dict) and sub.get("Text"):
                    items.append(
                        {
                            "title": (sub.get("Text") or "")[:80],
                            "url": sub.get("FirstURL") or "",
                            "snippet": sub.get("Text") or "",
                        }
                    )
    if not items:
        return {
            "ok": True,
            "observation": f"未找到与「{query}」相关的公开摘要，可换关键词再试。",
            "data": {"results": [], "query": query},
        }
    return _format_results(items[:max_results], query)


def _format_results(items: list[dict[str, Any]], query: str) -> dict[str, Any]:
    lines = []
    normalized = []
    for i, it in enumerate(items, start=1):
        title = str(it.get("title") or "")
        url = str(it.get("url") or "")
        snippet = str(it.get("snippet") or it.get("body") or "")
        normalized.append({"title": title, "url": url, "snippet": snippet})
        lines.append(f"[{i}] {title}\nURL: {url}\n{snippet}")
    return {
        "ok": True,
        "observation": f"搜索「{query}」结果：\n" + "\n\n".join(lines),
        "data": {"results": normalized, "query": query},
    }

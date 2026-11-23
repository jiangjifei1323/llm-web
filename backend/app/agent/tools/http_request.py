"""白名单 HTTP 请求工具（risk=external，默认需 HITL）。"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .. import config
from .base import ToolContext, register


def _host_allowed(hostname: str) -> bool:
    host = (hostname or "").lower().strip(".")
    if not host or not config.HTTP_ALLOWLIST:
        return False
    for allowed in config.HTTP_ALLOWLIST:
        if host == allowed or host.endswith("." + allowed):
            return True
    return False


@register(
    name="http_request",
    description=(
        "向白名单域名发起 HTTP GET/POST 请求，获取公开 JSON 或文本。"
        "仅用于已配置允许的主机；禁止探测内网或未授权系统。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "完整 URL，须为 http/https 且主机在白名单内",
            },
            "method": {
                "type": "string",
                "enum": ["GET", "POST"],
                "description": "HTTP 方法，默认 GET",
                "default": "GET",
            },
            "body": {
                "type": "string",
                "description": "POST 时的请求体（文本/JSON 字符串），可选",
            },
        },
        "required": ["url"],
    },
    risk="external",
    timeout_seconds=20.0,
    idempotent=False,
)
def http_request(_ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if not config.HTTP_ALLOWLIST:
        return {
            "ok": False,
            "observation": "未配置 HTTP_ALLOWLIST，拒绝执行 http_request",
            "error": "not_configured",
        }
    url = str(args.get("url") or "").strip()
    method = str(args.get("method") or "GET").upper()
    body = args.get("body")
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "observation": f"URL 无效：{exc}", "error": "invalid_args"}
    if parsed.scheme not in ("http", "https"):
        return {
            "ok": False,
            "observation": "仅允许 http/https",
            "error": "forbidden",
        }
    if not _host_allowed(parsed.hostname or ""):
        return {
            "ok": False,
            "observation": (
                f"主机 {parsed.hostname} 不在白名单 "
                f"（{', '.join(config.HTTP_ALLOWLIST)}）"
            ),
            "error": "forbidden",
        }

    data = None
    headers = {"User-Agent": "llm-web-agent/0.1", "Accept": "application/json,text/plain,*/*"}
    if method == "POST":
        raw_body = "" if body is None else str(body)
        data = raw_body.encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = getattr(resp, "status", 200)
            text = resp.read(64_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        err_body = exc.read(4000).decode("utf-8", errors="replace") if exc.fp else ""
        return {
            "ok": False,
            "observation": f"HTTP {exc.code}：{err_body[:500]}",
            "error": "http_error",
            "data": {"status": exc.code},
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "observation": f"请求失败：{exc}",
            "error": "runtime",
        }

    # 尝试解析 JSON，否则返回截断文本
    parsed_json: Any = None
    try:
        parsed_json = json.loads(text)
        observation = json.dumps(parsed_json, ensure_ascii=False)[:4000]
    except json.JSONDecodeError:
        observation = text[:4000]

    return {
        "ok": True,
        "observation": f"HTTP {status} 响应：\n{observation}",
        "data": {"status": status, "body": parsed_json if parsed_json is not None else text[:2000]},
    }

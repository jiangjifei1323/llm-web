"""工具协议、注册表、Schema 校验、超时与截断。"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Literal

from .. import config

Risk = Literal["read", "write", "external"]

DEFAULT_MAX_OBSERVATION_CHARS = 6000

_JSON_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": list,
}

_SECRET_PATTERNS = [
    re.compile(
        r"(?i)\b(sk|pk|api[_-]?key|access[_-]?token|refresh[_-]?token)\b\s*[:=]\s*\S+"
    ),
    re.compile(r"\b[A-Za-z0-9_\-]{32,}\.[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)"),
]


class ToolSpecError(Exception):
    """工具注册或入参不合法（作为观察回灌，不直接判任务失败）。"""


@dataclass
class ToolContext:
    """工具执行上下文：鉴权信息在工具内再查，不信任模型传来的权限字段。"""

    user_id: int
    task_id: str
    conversation_id: str
    kb_ids: list[int] = field(default_factory=list)
    is_admin: bool = False


@dataclass
class ToolResult:
    ok: bool
    observation: str
    data: dict[str, Any] | None = None
    error: str | None = None
    latency_ms: int = 0


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    run: Callable[[ToolContext, dict[str, Any]], Any]
    risk: Risk = "read"
    timeout_seconds: float = 30.0
    idempotent: bool = True
    max_observation_chars: int = DEFAULT_MAX_OBSERVATION_CHARS

    def as_openai_tool(self) -> dict[str, Any]:
        """OpenAI / LangChain bind_tools 可用的 function 描述。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


TOOL_REGISTRY: dict[str, Tool] = {}


def register(
    *,
    name: str,
    description: str,
    input_schema: dict[str, Any],
    risk: Risk = "read",
    timeout_seconds: float = 30.0,
    idempotent: bool = True,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def deco(func: Callable[..., Any]) -> Callable[..., Any]:
        if name in TOOL_REGISTRY:
            raise ValueError(f"工具重复注册：{name}")
        TOOL_REGISTRY[name] = Tool(
            name=name,
            description=description,
            input_schema=input_schema,
            run=func,
            risk=risk,
            timeout_seconds=timeout_seconds,
            idempotent=idempotent,
        )
        return func

    return deco


def enabled_tools() -> list[Tool]:
    if not config.TOOLS_ENABLED:
        return list(TOOL_REGISTRY.values())
    return [TOOL_REGISTRY[n] for n in config.TOOLS_ENABLED if n in TOOL_REGISTRY]


def is_enabled(name: str) -> bool:
    if name not in TOOL_REGISTRY:
        return False
    if not config.TOOLS_ENABLED:
        return True
    return name in config.TOOLS_ENABLED


def validate_args(schema: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(args, dict):
        raise ToolSpecError(f"入参必须是对象，收到 {type(args).__name__}")
    props = schema.get("properties") or {}
    required = schema.get("required") or []
    for key in required:
        if key not in args or args[key] is None:
            raise ToolSpecError(f"缺少必填参数：{key}")
    unknown = [k for k in args if k not in props]
    if unknown:
        allowed = ", ".join(props) or "无"
        raise ToolSpecError(f"未知参数：{', '.join(unknown)}（可用：{allowed}）")

    normalized = dict(args)
    for key, prop in props.items():
        if key not in normalized:
            if "default" in prop:
                normalized[key] = prop["default"]
            continue
        expected = prop.get("type")
        value = normalized[key]
        if expected and expected in _JSON_TYPE_MAP:
            py_type = _JSON_TYPE_MAP[expected]
            if expected == "integer" and isinstance(value, bool):
                raise ToolSpecError(f"参数 {key} 类型应为 integer")
            if expected == "integer" and isinstance(value, float) and value.is_integer():
                value = int(value)
                normalized[key] = value
            if not isinstance(value, py_type):
                raise ToolSpecError(
                    f"参数 {key} 类型应为 {expected}，收到 {type(value).__name__}"
                )
        enum = prop.get("enum")
        if enum is not None and value not in enum:
            raise ToolSpecError(f"参数 {key} 取值应为 {enum} 之一")
    return normalized


def mask_secrets(text: str) -> str:
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub("***", out)
    return out


def truncate_observation(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    keep = max(max_chars // 2 - 20, 100)
    return (
        f"{text[:keep]}\n……（已截断，原文共 {len(text)} 字）……\n{text[-keep:]}"
    )


def _to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _error(code: str, message: str) -> str:
    return json.dumps({"error": code, "message": message}, ensure_ascii=False)


def get_tool(name: str) -> Tool | None:
    return TOOL_REGISTRY.get(name)


def needs_approval(name: str) -> bool:
    """是否需人工审批（按 HITL_RISKS 配置）。"""
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        return False
    return config.risk_needs_approval(tool.risk)


def execute(
    name: str,
    ctx: ToolContext,
    args: dict[str, Any] | None,
    *,
    approved: bool = False,
) -> ToolResult:
    """执行工具；任何失败都转成 ok=False 的观察。

    approved=True 表示已通过 HITL，允许执行 write/external。
    """
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        return ToolResult(ok=False, observation=_error("unknown_tool", f"未知工具：{name}"), error="unknown_tool")
    if not is_enabled(name):
        return ToolResult(
            ok=False,
            observation=_error("not_enabled", f"工具 {name} 未被授权"),
            error="not_enabled",
        )
    if needs_approval(name) and not approved:
        return ToolResult(
            ok=False,
            observation=_error(
                "approval_required",
                f"工具 {name} 风险级别为 {tool.risk}，需要人工审批后再执行",
            ),
            error="approval_required",
        )

    start = time.perf_counter()
    try:
        normalized = validate_args(tool.input_schema, args or {})
    except ToolSpecError as exc:
        return ToolResult(
            ok=False,
            observation=_error("invalid_args", str(exc)),
            error="invalid_args",
            latency_ms=int((time.perf_counter() - start) * 1000),
        )

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(tool.run, ctx, normalized)
            raw = future.result(timeout=tool.timeout_seconds)
    except FutureTimeout:
        return ToolResult(
            ok=False,
            observation=_error("timeout", f"工具 {name} 执行超时（>{tool.timeout_seconds}s）"),
            error="timeout",
            latency_ms=int((time.perf_counter() - start) * 1000),
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult(
            ok=False,
            observation=_error("runtime", f"工具 {name} 执行失败：{exc}"),
            error="runtime",
            latency_ms=int((time.perf_counter() - start) * 1000),
        )

    data: dict[str, Any] | None = None
    if isinstance(raw, ToolResult):
        observation = raw.observation
        data = raw.data
        ok = raw.ok
        err = raw.error
    elif isinstance(raw, dict) and "observation" in raw:
        observation = _to_text(raw.get("observation"))
        data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        ok = bool(raw.get("ok", True))
        err = raw.get("error")
    else:
        observation = _to_text(raw)
        ok = True
        err = None

    observation = truncate_observation(mask_secrets(observation), tool.max_observation_chars)
    return ToolResult(
        ok=ok,
        observation=observation,
        data=data if isinstance(data, dict) else None,
        error=str(err) if err else None,
        latency_ms=int((time.perf_counter() - start) * 1000),
    )


def digest(observation: str, limit: int = 160) -> str:
    text = " ".join(observation.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"

"""Agent 运行时配置：全部走环境变量，模块导入时读取一次。"""

from __future__ import annotations

import os


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


AGENT_ENABLED = _flag("AGENT_ENABLED", True)
AGENT_WORKERS = max(1, _int("AGENT_WORKERS", 2))
AGENT_MAX_STEPS = max(1, _int("AGENT_MAX_STEPS", 8))
AGENT_MAX_LLM_CALLS = max(1, _int("AGENT_MAX_LLM_CALLS", 20))
AGENT_MAX_TOOL_CALLS = max(1, _int("AGENT_MAX_TOOL_CALLS", 30))
AGENT_MAX_TOKENS = max(1000, _int("AGENT_MAX_TOKENS", 100_000))
AGENT_DEADLINE_MINUTES = max(1, _int("AGENT_DEADLINE_MINUTES", 10))
AGENT_SSE_HEARTBEAT_SECONDS = max(5, _int("AGENT_SSE_HEARTBEAT_SECONDS", 15))
AGENT_EVENT_POLL_MS = max(100, _int("AGENT_EVENT_POLL_MS", 400))
# 执行中因工具失败触发的重规划次数上限
AGENT_MAX_REPLANS = max(0, _int("AGENT_MAX_REPLANS", 2))
# 进程重启后对未完成任务的自动重试次数上限
AGENT_MAX_RETRIES = max(0, _int("AGENT_MAX_RETRIES", 1))
# 需要人工审批的风险级别（逗号分隔）：read / write / external
_HITL_RAW = os.getenv("HITL_RISKS", "write,external").strip()
HITL_RISKS: tuple[str, ...] = tuple(
    x.strip() for x in _HITL_RAW.split(",") if x.strip()
)
# 审批超时（分钟）：超时后任务失败；0 表示不超时
HITL_TIMEOUT_MINUTES = max(0, _int("HITL_TIMEOUT_MINUTES", 30))

# 逗号分隔的工具白名单；空则启用全部已注册工具
_TOOLS_RAW = os.getenv("TOOLS_ENABLED", "datetime_calc,kb_search,kb_list").strip()
TOOLS_ENABLED: tuple[str, ...] = tuple(
    x.strip() for x in _TOOLS_RAW.split(",") if x.strip()
)

# 可选外部工具
WEB_SEARCH_API_KEY = os.getenv("WEB_SEARCH_API_KEY", "").strip()
WEB_SEARCH_BASE_URL = os.getenv(
    "WEB_SEARCH_BASE_URL", "https://api.duckduckgo.com/"
).strip()
# http_request 允许访问的域名白名单（逗号分隔）；空则拒绝全部
_HTTP_ALLOW = os.getenv("HTTP_ALLOWLIST", "").strip()
HTTP_ALLOWLIST: tuple[str, ...] = tuple(
    x.strip().lower() for x in _HTTP_ALLOW.split(",") if x.strip()
)


def deadline_seconds() -> int:
    return AGENT_DEADLINE_MINUTES * 60


def risk_needs_approval(risk: str) -> bool:
    return risk in HITL_RISKS

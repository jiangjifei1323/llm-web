"""M1 规划器：优先 LLM 生成/修订计划，失败时回退规则模板。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..llm import chat_completion, is_configured
from .tools import enabled_tools

logger = logging.getLogger(__name__)

_DATE_HINT = re.compile(
    r"(今天|明天|后天|星期|周几|日期|相差|几天|倒计时|天后|之前|之后|"
    r"计算|算出|\d{4}[-/]\d{1,2}[-/]\d{1,2})",
    re.IGNORECASE,
)
_MATH_HINT = re.compile(r"(\d+\s*[+\-*/×÷]\s*\d+|计算|算一下)")

_PLAN_SYSTEM = """你是任务规划助手。根据用户目标与可用工具，输出一份可执行计划。
只输出 JSON 数组（不要 Markdown 代码块、不要解释），元素字段：
- id: 短字符串，如 s1、s2
- title: 中文步骤标题
- kind: "tool" 或 "final"
- tools: 本步可能用到的工具名数组（必须来自可用工具列表；final 步为空数组）
- success: 本步成功标准（一句话）

约束：
1. 最后一步必须 kind=final，且 tools=[]
2. 步骤数量 2～6
3. 需要精确日期/数值时加入 datetime_calc；需要知识库时加入 kb_list/kb_search
4. 不要发明不存在的工具名
"""


def _available_tool_names() -> list[str]:
    return [t.name for t in enabled_tools()]


def _tool_catalog_text() -> str:
    lines = []
    for t in enabled_tools():
        lines.append(f"- {t.name}: {t.description}")
    return "\n".join(lines) or "- （无可用工具）"


def rule_based_plan(message: str, kb_ids: list[int]) -> list[dict]:
    """规则回退计划（与 M0 一致）。"""
    steps: list[dict] = []
    if kb_ids:
        steps.append(
            {
                "id": "s_kb",
                "title": "检索知识库",
                "kind": "tool",
                "tools": ["kb_search"],
                "success": "获得与问题相关的参考资料",
            }
        )
    else:
        steps.append(
            {
                "id": "s_kb_optional",
                "title": "按需检索知识（如有）",
                "kind": "tool",
                "tools": ["kb_list", "kb_search"],
                "success": "确认是否需要知识库资料",
            }
        )

    if _DATE_HINT.search(message or "") or _MATH_HINT.search(message or ""):
        steps.append(
            {
                "id": "s_calc",
                "title": "日期或数值计算",
                "kind": "tool",
                "tools": ["datetime_calc"],
                "success": "得到精确计算结果",
            }
        )

    steps.append(
        {
            "id": "s_final",
            "title": "汇总作答",
            "kind": "final",
            "tools": [],
            "success": "给出覆盖用户需求的最终回答",
        }
    )
    return steps


def _extract_json_array(text: str) -> list[Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    # 去掉可能的 ```json 包裹
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if fence:
        raw = fence.group(1).strip()
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else None
    except json.JSONDecodeError:
        start, end = raw.find("["), raw.rfind("]")
        if start >= 0 and end > start:
            try:
                data = json.loads(raw[start : end + 1])
                return data if isinstance(data, list) else None
            except json.JSONDecodeError:
                return None
    return None


def normalize_plan(raw: list[Any] | None) -> list[dict] | None:
    """校验并归一化计划；非法则返回 None。"""
    if not raw or not isinstance(raw, list):
        return None
    allowed = set(_available_tool_names())
    steps: list[dict] = []
    for i, item in enumerate(raw[:8]):
        if not isinstance(item, dict):
            return None
        title = str(item.get("title") or "").strip()
        if not title:
            return None
        kind = str(item.get("kind") or "tool").strip().lower()
        if kind not in ("tool", "final"):
            kind = "tool"
        tools_raw = item.get("tools") or []
        if not isinstance(tools_raw, list):
            tools_raw = []
        tools = [str(t) for t in tools_raw if str(t) in allowed]
        step_id = str(item.get("id") or f"s{i + 1}").strip() or f"s{i + 1}"
        success = str(item.get("success") or ("完成该步骤" if kind == "tool" else "给出最终回答"))
        if kind == "final":
            tools = []
        steps.append(
            {
                "id": step_id,
                "title": title[:200],
                "kind": kind,
                "tools": tools,
                "success": success[:200],
            }
        )
    if not steps:
        return None
    # 保证最后一步是 final
    if steps[-1]["kind"] != "final":
        steps.append(
            {
                "id": "s_final",
                "title": "汇总作答",
                "kind": "final",
                "tools": [],
                "success": "给出覆盖用户需求的最终回答",
            }
        )
    else:
        steps[-1]["tools"] = []
    # 去掉 final 之前重复的空 tool 步
    if len(steps) < 2:
        return None
    return steps


def build_plan_llm(
    message: str,
    kb_ids: list[int],
    *,
    usage_out: dict | None = None,
) -> tuple[list[dict], str]:
    """LLM 规划；返回 (plan, source) source=llm|rules。"""
    fallback = rule_based_plan(message, kb_ids)
    if not is_configured():
        return fallback, "rules"

    user_prompt = (
        f"可用工具：\n{_tool_catalog_text()}\n\n"
        f"用户预选知识库 id：{kb_ids or '无'}\n\n"
        f"用户目标：\n{message}\n"
    )
    try:
        result = chat_completion(
            [
                {"role": "system", "content": _PLAN_SYSTEM},
                {"role": "user", "content": user_prompt},
            ]
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM 规划失败，回退规则计划：%s", exc)
        return fallback, "rules"

    if usage_out is not None:
        usage_out["input_tokens"] = result.usage.input_tokens
        usage_out["output_tokens"] = result.usage.output_tokens
        usage_out["total_tokens"] = result.usage.total_tokens
        usage_out["model"] = result.model
        usage_out["latency_ms"] = result.latency_ms
        usage_out["raw"] = result.answer

    plan = normalize_plan(_extract_json_array(result.answer))
    if plan is None:
        logger.warning("LLM 规划结果无法解析，回退规则计划")
        return fallback, "rules"
    return plan, "llm"


def replan_llm(
    message: str,
    kb_ids: list[int],
    previous_plan: list[dict],
    failure_reason: str,
    observations: list[str],
    *,
    usage_out: dict | None = None,
) -> tuple[list[dict], str]:
    """失败后修订计划。"""
    fallback = rule_based_plan(message, kb_ids)
    if not is_configured():
        return fallback, "rules"

    obs_text = "\n".join(f"- {o}" for o in observations[-8:]) or "- （暂无）"
    user_prompt = (
        f"可用工具：\n{_tool_catalog_text()}\n\n"
        f"用户预选知识库 id：{kb_ids or '无'}\n\n"
        f"用户目标：\n{message}\n\n"
        f"原计划：\n{json.dumps(previous_plan, ensure_ascii=False)}\n\n"
        f"失败/不足原因：{failure_reason}\n\n"
        f"已有观察摘要：\n{obs_text}\n\n"
        "请输出修订后的完整计划 JSON 数组。"
    )
    try:
        result = chat_completion(
            [
                {
                    "role": "system",
                    "content": _PLAN_SYSTEM
                    + "\n这是一次重规划：保留仍然有效的步骤思路，修正失败路径，避免重复无效工具调用。",
                },
                {"role": "user", "content": user_prompt},
            ]
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM 重规划失败，回退规则计划：%s", exc)
        return fallback, "rules"

    if usage_out is not None:
        usage_out["input_tokens"] = result.usage.input_tokens
        usage_out["output_tokens"] = result.usage.output_tokens
        usage_out["total_tokens"] = result.usage.total_tokens
        usage_out["model"] = result.model
        usage_out["latency_ms"] = result.latency_ms
        usage_out["raw"] = result.answer

    plan = normalize_plan(_extract_json_array(result.answer))
    if plan is None:
        return fallback, "rules"
    return plan, "llm"


# 兼容旧导入名
def build_plan(message: str, kb_ids: list[int]) -> list[dict]:
    plan, _ = build_plan_llm(message, kb_ids)
    return plan

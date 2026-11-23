"""轻量 Router：决定走简单问答还是 Agent 任务。"""

from __future__ import annotations

import re

# 倾向需要工具的启发式关键词（中英）
_AGENT_HINTS = re.compile(
    r"("
    r"计算|算出|算一下|加减|相差|几天|星期|周几|倒计时|距离.*天|"
    r"检索|查一下|搜索|整理|对比|汇总|分析|根据知识库|结合知识库|多步|"
    r"calculate|countdown|compare|analyze|search"
    r")",
    re.IGNORECASE,
)


def resolve_mode(requested: str, message: str, kb_ids: list[int]) -> str:
    """返回实际模式：chat | agent。

    requested: auto / agent / chat
    """
    mode = (requested or "auto").strip().lower()
    if mode == "agent":
        return "agent"
    if mode == "chat":
        return "chat"
    # auto
    if kb_ids:
        return "agent"
    if _AGENT_HINTS.search(message or ""):
        return "agent"
    # 较长指令也倾向 agent
    if len((message or "").strip()) >= 80:
        return "agent"
    return "chat"

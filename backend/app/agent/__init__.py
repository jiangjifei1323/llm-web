"""任务型 Agent：规划、工具调用与同进程执行。"""

from .router import resolve_mode
from .runtime import (
    get_runner,
    make_deadline,
    resolve_approval,
    start_runner,
    stop_runner,
    submit_task,
)

__all__ = [
    "get_runner",
    "make_deadline",
    "resolve_approval",
    "resolve_mode",
    "start_runner",
    "stop_runner",
    "submit_task",
]

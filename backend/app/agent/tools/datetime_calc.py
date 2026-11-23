"""日期/时间与安全四则运算工具。"""

from __future__ import annotations

import ast
import operator
from datetime import date, datetime, timedelta
from typing import Any

from .base import ToolContext, ToolSpecError, register

_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_MAX_POW = 12


def _parse_date(value: str | None, field: str) -> date:
    if not value:
        return date.today()
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ToolSpecError(f"{field} 应为 YYYY-MM-DD 格式，收到：{value}") from exc


def _eval_math(node: ast.AST) -> float | int:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_math(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left = _eval_math(node.left)
        right = _eval_math(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_POW:
            raise ToolSpecError("指数过大，已拒绝计算")
        return _BIN_OPS[type(node.op)](left, right)
    raise ToolSpecError("表达式只支持数字与 + - * / // % ** 与括号")


def _calc(expression: str) -> float | int:
    text = expression.strip()
    if not text:
        raise ToolSpecError("expression 不能为空")
    if not all(ch in "0123456789.+-*/%() " for ch in text):
        raise ToolSpecError("表达式包含不支持的字符")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ToolSpecError(f"表达式无法解析：{exc.msg}") from exc
    return _eval_math(tree.body)


@register(
    name="datetime_calc",
    description=(
        "日期时间与四则运算计算器。"
        "用于「今天/某日期是星期几、两个日期相差几天、某日期加减 N 天后的日期、以及任意算术表达式」。"
        "需要精确数值或日期推算时必须调用它，不要靠心算。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["now", "date_add", "date_diff", "calc"],
                "description": "now=当前日期时间；date_add=日期加减天数；date_diff=两日期相差天数；calc=四则运算",
            },
            "date": {
                "type": "string",
                "description": "起始/参照日期 YYYY-MM-DD；省略表示今天",
            },
            "date2": {
                "type": "string",
                "description": "date_diff 的结束日期 YYYY-MM-DD",
            },
            "days": {
                "type": "integer",
                "description": "date_add 的天数，可为负数",
            },
            "expression": {
                "type": "string",
                "description": "calc 的算术表达式，如 (128+64)*3/8",
            },
        },
        "required": ["action"],
    },
    risk="read",
    timeout_seconds=5.0,
)
def datetime_calc(_ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    action = args["action"]
    if action == "now":
        now = datetime.now().replace(microsecond=0)
        return {
            "ok": True,
            "observation": (
                f"当前时间：{now.isoformat(sep=' ')}，"
                f"{_WEEKDAYS[now.weekday()]}，年份 {now.year}"
            ),
            "data": {
                "datetime": now.isoformat(sep=" "),
                "weekday": _WEEKDAYS[now.weekday()],
            },
        }

    if action == "date_add":
        if "days" not in args:
            raise ToolSpecError("date_add 需要 days 参数")
        base = _parse_date(args.get("date"), "date")
        result = base + timedelta(days=int(args["days"]))
        return {
            "ok": True,
            "observation": (
                f"{base.isoformat()} 加上 {int(args['days'])} 天 = "
                f"{result.isoformat()}（{_WEEKDAYS[result.weekday()]}）"
            ),
            "data": {"date": result.isoformat(), "weekday": _WEEKDAYS[result.weekday()]},
        }

    if action == "date_diff":
        if not args.get("date2"):
            raise ToolSpecError("date_diff 需要 date 与 date2 两个日期")
        d1 = _parse_date(args.get("date"), "date")
        d2 = _parse_date(args.get("date2"), "date2")
        delta = (d2 - d1).days
        return {
            "ok": True,
            "observation": f"{d1.isoformat()} 到 {d2.isoformat()} 相差 {delta} 天",
            "data": {"days": delta, "date": d1.isoformat(), "date2": d2.isoformat()},
        }

    if action == "calc":
        value = _calc(str(args.get("expression") or ""))
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return {
            "ok": True,
            "observation": f"计算结果：{value}",
            "data": {"value": value},
        }

    raise ToolSpecError(f"不支持的 action：{action}")

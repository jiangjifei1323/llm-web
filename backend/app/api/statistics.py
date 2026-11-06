"""后台统计接口：Token 用量统计。"""

from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from .. import auth, db
from ..db import User
from ..db.models import LLMCall

router = APIRouter(prefix="/api/admin/statistics", tags=["后台统计"])


PeriodType = Literal["day", "week", "month"]


class StatisticsItem(BaseModel):
    """单个统计项"""
    time: str  # 时间标签（小时或日期）
    input_tokens: int
    output_tokens: int
    total_tokens: int
    call_count: int


class StatisticsSummary(BaseModel):
    """汇总统计"""
    total_input: int
    total_output: int
    total_tokens: int
    total_calls: int


class StatisticsResponse(BaseModel):
    """统计响应"""
    period: str
    unit: str  # "hour" 或 "day"
    data: list[StatisticsItem]
    summary: StatisticsSummary


@router.get("/token", response_model=StatisticsResponse)
def token_statistics(
    period: PeriodType = Query(default="day", description="统计周期：day/week/month"),
    date: str = Query(default="", description="基准日期 YYYY-MM-DD，默认今天"),
    _: User = Depends(auth.require_admin),
) -> StatisticsResponse:
    """获取 Token 用量统计（按小时/天聚合）。"""
    # 解析基准日期
    if date:
        try:
            base_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="日期格式错误，应为 YYYY-MM-DD")
    else:
        base_date = datetime.now().date()

    # 根据周期确定时间范围和分组
    if period == "day":
        # 按小时统计：0-23 时
        start_dt = datetime.combine(base_date, datetime.min.time())
        end_dt = start_dt + timedelta(days=1)
        unit = "hour"
        time_labels = [f"{h:02d}:00" for h in range(24)]
    elif period == "week":
        # 按天统计：最近 7 天
        start_dt = datetime.combine(base_date - timedelta(days=6), datetime.min.time())
        end_dt = datetime.combine(base_date + timedelta(days=1), datetime.min.time())
        unit = "day"
        time_labels = [(base_date - timedelta(days=i)).strftime("%m-%d") for i in range(6, -1, -1)]
    else:  # month
        # 按天统计：最近 30 天
        start_dt = datetime.combine(base_date - timedelta(days=29), datetime.min.time())
        end_dt = datetime.combine(base_date + timedelta(days=1), datetime.min.time())
        unit = "day"
        time_labels = [(base_date - timedelta(days=i)).strftime("%m-%d") for i in range(29, -1, -1)]

    # 查询数据库
    with db.SessionLocal() as session:
        if period == "day":
            # 按小时分组
            stmt = select(
                func.extract("hour", LLMCall.created_at).label("time_slot"),
                func.coalesce(func.sum(LLMCall.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(LLMCall.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(LLMCall.total_tokens), 0).label("total_tokens"),
                func.count(LLMCall.id).label("call_count"),
            ).where(
                LLMCall.created_at >= start_dt,
                LLMCall.created_at < end_dt,
                LLMCall.success.is_(True),
            ).group_by(
                func.extract("hour", LLMCall.created_at)
            ).order_by(
                "time_slot"
            )
        else:
            # 按天分组
            stmt = select(
                func.date(LLMCall.created_at).label("time_slot"),
                func.coalesce(func.sum(LLMCall.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(LLMCall.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(LLMCall.total_tokens), 0).label("total_tokens"),
                func.count(LLMCall.id).label("call_count"),
            ).where(
                LLMCall.created_at >= start_dt,
                LLMCall.created_at < end_dt,
                LLMCall.success.is_(True),
            ).group_by(
                func.date(LLMCall.created_at)
            ).order_by(
                "time_slot"
            )

        rows = session.execute(stmt).all()

    # 构建结果数据
    data_map = {}
    for row in rows:
        if period == "day":
            key = f"{int(row.time_slot):02d}:00"
        else:
            key = row.time_slot.strftime("%m-%d") if hasattr(row.time_slot, "strftime") else str(row.time_slot)
        data_map[key] = {
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "total_tokens": row.total_tokens,
            "call_count": row.call_count,
        }

    # 填充所有时间槽（无数据的填 0）
    data = []
    for label in time_labels:
        slot = data_map.get(label, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "call_count": 0})
        data.append(StatisticsItem(time=label, **slot))

    # 计算汇总
    summary = StatisticsSummary(
        total_input=sum(d.input_tokens for d in data),
        total_output=sum(d.output_tokens for d in data),
        total_tokens=sum(d.total_tokens for d in data),
        total_calls=sum(d.call_count for d in data),
    )

    return StatisticsResponse(
        period=period,
        unit=unit,
        data=data,
        summary=summary,
    )

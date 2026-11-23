"""Agent 任务 / 步骤 / 事件 / 工具调用的数据访问。"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from .base import SessionLocal, _now
from .models import AgentEvent, AgentStep, AgentTask, AgentToolCall


def new_task_id() -> str:
    return str(uuid4())


def create_task(
    *,
    task_id: str,
    conversation_id: str,
    user_id: int,
    input_text: str,
    kb_ids: list[int],
    mode: str = "agent",
    deadline_at=None,
) -> AgentTask:
    with SessionLocal() as session:
        task = AgentTask(
            id=task_id,
            conversation_id=conversation_id,
            user_id=user_id,
            input=input_text,
            status="created",
            mode=mode,
            kb_ids_json=json.dumps(kb_ids, ensure_ascii=False),
            deadline_at=deadline_at,
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        return task


def get_task(task_id: str) -> AgentTask | None:
    with SessionLocal() as session:
        return session.get(AgentTask, task_id)


def list_task_snapshot(task_id: str) -> dict[str, Any] | None:
    with SessionLocal() as session:
        task = session.get(AgentTask, task_id)
        if task is None:
            return None
        return _task_to_dict(task)


def _task_to_dict(task: AgentTask) -> dict[str, Any]:
    plan = None
    if task.plan_json:
        try:
            plan = json.loads(task.plan_json)
        except ValueError:
            plan = None
    try:
        kb_ids = json.loads(task.kb_ids_json or "[]")
    except ValueError:
        kb_ids = []
    return {
        "id": task.id,
        "conversation_id": task.conversation_id,
        "user_id": task.user_id,
        "input": task.input,
        "status": task.status,
        "mode": task.mode,
        "kb_ids": kb_ids if isinstance(kb_ids, list) else [],
        "plan": plan,
        "used_steps": task.used_steps,
        "used_llm_calls": task.used_llm_calls,
        "used_tool_calls": task.used_tool_calls,
        "used_tokens": task.used_tokens,
        "deadline_at": task.deadline_at,
        "error": task.error,
        "result_message_id": task.result_message_id,
        "cancel_requested": task.cancel_requested,
        "retry_count": getattr(task, "retry_count", 0) or 0,
        "replan_count": getattr(task, "replan_count", 0) or 0,
        "pending_approval": _loads_json(getattr(task, "pending_approval_json", None)),
        "has_checkpoint": bool(getattr(task, "checkpoint_json", None)),
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def _loads_json(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def get_checkpoint(task_id: str) -> dict[str, Any] | None:
    task = get_task(task_id)
    if task is None:
        return None
    return _loads_json(getattr(task, "checkpoint_json", None))


def set_checkpoint(task_id: str, checkpoint: dict[str, Any] | None) -> None:
    update_task(
        task_id,
        checkpoint_json=json.dumps(checkpoint, ensure_ascii=False)
        if checkpoint is not None
        else None,
    )


def get_pending_approval(task_id: str) -> dict[str, Any] | None:
    task = get_task(task_id)
    if task is None:
        return None
    return _loads_json(getattr(task, "pending_approval_json", None))


def set_pending_approval(task_id: str, payload: dict[str, Any] | None) -> None:
    update_task(
        task_id,
        pending_approval_json=json.dumps(payload, ensure_ascii=False)
        if payload is not None
        else None,
    )


def list_tool_calls(task_id: str) -> list[dict[str, Any]]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(AgentToolCall)
            .where(AgentToolCall.task_id == task_id)
            .order_by(AgentToolCall.id.asc())
        ).all()
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                args = json.loads(r.args_json or "{}")
            except ValueError:
                args = {}
            out.append(
                {
                    "id": r.id,
                    "tool_name": r.tool_name,
                    "args": args,
                    "observation": r.observation,
                    "ok": r.ok,
                    "risk": r.risk,
                    "latency_ms": r.latency_ms,
                    "step_key": r.step_key,
                    "created_at": r.created_at,
                }
            )
        return out


def build_audit_export(task_id: str) -> dict[str, Any] | None:
    """汇总任务审计包：任务快照 + 步骤 + 事件 + 工具调用。"""
    snap = list_task_snapshot(task_id)
    if snap is None:
        return None
    return {
        "task": snap,
        "steps": list_steps(task_id),
        "events": list_events_after(task_id, after_id=0, limit=10_000),
        "tool_calls": list_tool_calls(task_id),
        "exported_at": _now().isoformat(sep=" ", timespec="seconds"),
    }


def update_task(task_id: str, **fields: Any) -> AgentTask | None:
    """更新任务字段；自动刷新 updated_at。"""
    with SessionLocal() as session:
        task = session.get(AgentTask, task_id)
        if task is None:
            return None
        for key, value in fields.items():
            if hasattr(task, key):
                setattr(task, key, value)
        task.updated_at = _now()
        session.commit()
        session.refresh(task)
        return task


def request_cancel(task_id: str) -> AgentTask | None:
    with SessionLocal() as session:
        task = session.get(AgentTask, task_id)
        if task is None:
            return None
        if task.status in ("succeeded", "failed", "cancelled"):
            return task
        task.cancel_requested = True
        task.updated_at = _now()
        session.commit()
        session.refresh(task)
        return task


def mark_cancelled(task_id: str, error: str | None = None) -> None:
    update_task(
        task_id,
        status="cancelled",
        error=error or "用户取消",
        cancel_requested=True,
    )


def append_event(task_id: str, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, int]:
    """追加事件，返回 {id, seq}。"""
    with SessionLocal() as session:
        max_seq = session.scalar(
            select(func.coalesce(func.max(AgentEvent.seq), 0)).where(
                AgentEvent.task_id == task_id
            )
        )
        seq = int(max_seq or 0) + 1
        row = AgentEvent(
            task_id=task_id,
            seq=seq,
            type=event_type,
            payload=json.dumps(payload or {}, ensure_ascii=False),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return {"id": row.id, "seq": row.seq}


def list_events_after(task_id: str, after_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(AgentEvent)
            .where(AgentEvent.task_id == task_id, AgentEvent.id > after_id)
            .order_by(AgentEvent.id.asc())
            .limit(limit)
        ).all()
        out: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row.payload or "{}")
            except ValueError:
                payload = {}
            out.append(
                {
                    "id": row.id,
                    "seq": row.seq,
                    "type": row.type,
                    "payload": payload,
                    "created_at": row.created_at.isoformat(sep=" ", timespec="seconds")
                    if row.created_at
                    else None,
                }
            )
        return out


def upsert_step(
    *,
    task_id: str,
    step_key: str,
    title: str,
    status: str,
    input_summary: str | None = None,
    output_summary: str | None = None,
    mark_started: bool = False,
    mark_finished: bool = False,
) -> int:
    with SessionLocal() as session:
        row = session.scalars(
            select(AgentStep).where(
                AgentStep.task_id == task_id, AgentStep.step_key == step_key
            )
        ).first()
        now = _now()
        if row is None:
            row = AgentStep(
                task_id=task_id,
                step_key=step_key,
                title=title,
                status=status,
                input_summary=input_summary,
                output_summary=output_summary,
                started_at=now if mark_started else None,
                finished_at=now if mark_finished else None,
            )
            session.add(row)
        else:
            row.title = title
            row.status = status
            if input_summary is not None:
                row.input_summary = input_summary
            if output_summary is not None:
                row.output_summary = output_summary
            if mark_started and row.started_at is None:
                row.started_at = now
            if mark_finished:
                row.finished_at = now
        session.commit()
        session.refresh(row)
        return row.id


def log_tool_call(
    *,
    task_id: str,
    tool_name: str,
    args: dict[str, Any],
    observation: str | None,
    ok: bool,
    risk: str = "read",
    latency_ms: int | None = None,
    step_key: str | None = None,
) -> int:
    with SessionLocal() as session:
        row = AgentToolCall(
            task_id=task_id,
            step_key=step_key,
            tool_name=tool_name,
            args_json=json.dumps(args, ensure_ascii=False),
            observation=observation,
            ok=ok,
            risk=risk,
            latency_ms=latency_ms,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id


def list_created_or_running_tasks(limit: int = 50) -> list[dict[str, Any]]:
    """启动时捞回未完成任务快照（含 retry_count）。"""
    with SessionLocal() as session:
        rows = session.scalars(
            select(AgentTask)
            .where(AgentTask.status.in_(["created", "running"]))
            .order_by(AgentTask.created_at.asc())
            .limit(limit)
        ).all()
        return [_task_to_dict(t) for t in rows]


def list_awaiting_approval_tasks(limit: int = 100) -> list[dict[str, Any]]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(AgentTask)
            .where(AgentTask.status == "awaiting_approval")
            .order_by(AgentTask.updated_at.asc())
            .limit(limit)
        ).all()
        return [_task_to_dict(t) for t in rows]


def list_created_or_running_task_ids(limit: int = 50) -> list[str]:
    return [t["id"] for t in list_created_or_running_tasks(limit)]


def list_steps(task_id: str) -> list[dict[str, Any]]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(AgentStep)
            .where(AgentStep.task_id == task_id)
            .order_by(AgentStep.id.asc())
        ).all()
        return [
            {
                "step_key": r.step_key,
                "title": r.title,
                "status": r.status,
                "input_summary": r.input_summary,
                "output_summary": r.output_summary,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
            }
            for r in rows
        ]


def agent_statistics_summary(start_dt, end_dt) -> dict[str, Any]:
    """管理后台：时间窗内 Agent 任务与用量汇总。"""
    with SessionLocal() as session:
        tasks = session.scalars(
            select(AgentTask).where(
                AgentTask.created_at >= start_dt,
                AgentTask.created_at < end_dt,
            )
        ).all()
        total = len(tasks)
        succeeded = sum(1 for t in tasks if t.status == "succeeded")
        failed = sum(1 for t in tasks if t.status == "failed")
        cancelled = sum(1 for t in tasks if t.status == "cancelled")
        running = sum(1 for t in tasks if t.status in ("created", "running"))
        total_steps = sum(t.used_steps or 0 for t in tasks)
        total_tool_calls = sum(t.used_tool_calls or 0 for t in tasks)
        total_tokens = sum(t.used_tokens or 0 for t in tasks)
        total_replans = sum(getattr(t, "replan_count", 0) or 0 for t in tasks)
        avg_steps = round(total_steps / total, 2) if total else 0.0
        avg_tokens = round(total_tokens / total, 2) if total else 0.0
        return {
            "task_count": total,
            "succeeded": succeeded,
            "failed": failed,
            "cancelled": cancelled,
            "running_or_pending": running,
            "total_steps": total_steps,
            "total_tool_calls": total_tool_calls,
            "total_tokens": total_tokens,
            "total_replans": total_replans,
            "avg_steps": avg_steps,
            "avg_tokens": avg_tokens,
            "success_rate": round(succeeded / total, 4) if total else 0.0,
        }

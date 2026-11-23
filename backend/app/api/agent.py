"""Agent 任务接口：创建、查询、取消、SSE 事件订阅。"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .. import auth, db
from ..agent import config, make_deadline, resolve_approval, resolve_mode, submit_task
from ..agent.tools import enabled_tools, mask_secrets
from ..db import User
from ..schemas.agent import (
    AgentApprovalRequest,
    AgentApprovalResponse,
    AgentCancelResponse,
    AgentTaskCreateRequest,
    AgentTaskCreateResponse,
    AgentTaskView,
)
from .deps import check_conversation_access, check_kb_access

router = APIRouter(prefix="/api/agent", tags=["Agent"])


def _ensure_conversation(conversation_id: str, user_id: int) -> None:
    """确保会话行存在；不写入空消息，标题等任务完成落库时再填。"""
    from ..db.base import SessionLocal
    from ..db.models import Conversation

    with SessionLocal() as session:
        conv = session.get(Conversation, conversation_id)
        if conv is None:
            session.add(Conversation(id=conversation_id, user_id=user_id, title=None))
            session.commit()
            return
        if conv.deleted_at is not None:
            conv.deleted_at = None
        if conv.user_id is None:
            conv.user_id = user_id
        session.commit()


def _require_task_access(task_id: str, user: User) -> dict:
    snap = db.list_task_snapshot(task_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if snap["user_id"] != user.id and not user.is_admin:
        raise HTTPException(status_code=403, detail="无权访问该任务")
    return snap


@router.get("/tools")
def list_tools(user: User = Depends(auth.get_current_user)) -> dict:
    """当前启用的工具清单（便于前端/调试展示）。"""
    _ = user
    return {
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "risk": t.risk,
                "input_schema": t.input_schema,
            }
            for t in enabled_tools()
        ]
    }


@router.post("/tasks", response_model=AgentTaskCreateResponse)
def create_task(
    req: AgentTaskCreateRequest,
    user: User = Depends(auth.get_current_user),
) -> AgentTaskCreateResponse:
    conversation_id = req.conversation_id or db.new_conversation_id()
    check_conversation_access(conversation_id, user)
    kb_ids = check_kb_access(req.kb_ids, user)
    mode = resolve_mode(req.mode, req.message, kb_ids)

    if mode == "chat":
        return AgentTaskCreateResponse(
            task_id=None,
            conversation_id=conversation_id,
            status=None,
            mode="chat",
        )

    if not config.AGENT_ENABLED:
        raise HTTPException(status_code=503, detail="Agent 未启用")

    task_id = db.new_task_id()
    _ensure_conversation(conversation_id, user.id)

    db.create_task(
        task_id=task_id,
        conversation_id=conversation_id,
        user_id=user.id,
        input_text=req.message,
        kb_ids=kb_ids,
        mode="agent",
        deadline_at=make_deadline(),
    )
    db.append_event(task_id, "task.created", {"conversation_id": conversation_id})
    if not submit_task(task_id):
        db.update_task(task_id, status="failed", error="Agent 执行器未启动")
        db.append_event(task_id, "task.failed", {"error": "Agent 执行器未启动"})
        raise HTTPException(status_code=503, detail="Agent 执行器未启动")

    return AgentTaskCreateResponse(
        task_id=task_id,
        conversation_id=conversation_id,
        status="created",
        mode="agent",
    )


@router.get("/tasks/{task_id}", response_model=AgentTaskView)
def get_task(task_id: str, user: User = Depends(auth.get_current_user)) -> AgentTaskView:
    snap = _require_task_access(task_id, user)
    return AgentTaskView(**snap)


@router.post("/tasks/{task_id}/cancel", response_model=AgentCancelResponse)
def cancel_task(
    task_id: str, user: User = Depends(auth.get_current_user)
) -> AgentCancelResponse:
    snap = _require_task_access(task_id, user)
    if snap["status"] in ("succeeded", "failed", "cancelled"):
        return AgentCancelResponse(
            id=task_id,
            status=snap["status"],
            cancel_requested=bool(snap.get("cancel_requested")),
        )
    task = db.request_cancel(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    # created / awaiting_approval：可立即终态取消
    if task.status in ("created", "awaiting_approval"):
        db.mark_cancelled(task_id)
        db.set_pending_approval(task_id, None)
        db.append_event(task_id, "task.cancelled", {"reason": "user_cancel"})
        return AgentCancelResponse(id=task_id, status="cancelled", cancel_requested=True)
    return AgentCancelResponse(
        id=task_id, status=task.status, cancel_requested=True
    )


@router.post("/tasks/{task_id}/approvals", response_model=AgentApprovalResponse)
def approve_task(
    task_id: str,
    req: AgentApprovalRequest,
    user: User = Depends(auth.get_current_user),
) -> AgentApprovalResponse:
    """人工审批：approve / reject / edit。"""
    _require_task_access(task_id, user)
    try:
        snap = resolve_approval(
            task_id,
            action=req.action,
            args=req.args,
            reason=req.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return AgentApprovalResponse(
        id=task_id,
        status=str(snap.get("status") or "running"),
        action=req.action,
    )


@router.get("/tasks/{task_id}/audit")
def export_audit(
    task_id: str, user: User = Depends(auth.get_current_user)
) -> JSONResponse:
    """导出任务审计包（步骤/事件/工具调用），观察再次脱敏。"""
    _require_task_access(task_id, user)
    payload = db.build_audit_export(task_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    for call in payload.get("tool_calls") or []:
        if isinstance(call.get("observation"), str):
            call["observation"] = mask_secrets(call["observation"])
    return JSONResponse(
        content=payload,
        headers={
            "Content-Disposition": f'attachment; filename="agent-audit-{task_id}.json"'
        },
    )


@router.get("/tasks/{task_id}/events")
def subscribe_events(
    task_id: str,
    request: Request,
    after: int = Query(0, ge=0, description="仅返回 id 大于该游标的事件"),
    user: User = Depends(auth.get_current_user),
) -> StreamingResponse:
    """SSE：按 agent_events.id 游标推送；支持 Last-Event-ID 重连。"""
    _require_task_access(task_id, user)

    last_event_id = after
    header_id = request.headers.get("last-event-id") or request.headers.get("Last-Event-ID")
    if header_id:
        try:
            last_event_id = max(last_event_id, int(header_id))
        except ValueError:
            pass

    terminal = {"succeeded", "failed", "cancelled"}

    def event_gen() -> Iterator[str]:
        nonlocal last_event_id
        heartbeat_every = config.AGENT_SSE_HEARTBEAT_SECONDS
        poll = config.AGENT_EVENT_POLL_MS / 1000.0
        last_beat = time.monotonic()

        while True:
            rows = db.list_events_after(task_id, after_id=last_event_id, limit=100)
            for row in rows:
                last_event_id = row["id"]
                payload = {
                    "type": row["type"],
                    "seq": row["seq"],
                    **(row.get("payload") or {}),
                }
                yield (
                    f"id: {row['id']}\n"
                    f"event: {row['type']}\n"
                    f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                )
                if row["type"] in ("task.completed", "task.failed", "task.cancelled"):
                    return

            snap = db.list_task_snapshot(task_id)
            if snap and snap["status"] in terminal and not rows:
                # 终态且无新事件：补一条快照（防止竞态漏事件）
                yield (
                    f"event: task.{ 'completed' if snap['status']=='succeeded' else snap['status'] }\n"
                    f"data: {json.dumps({'status': snap['status'], 'error': snap.get('error')}, ensure_ascii=False)}\n\n"
                )
                return

            now = time.monotonic()
            if now - last_beat >= heartbeat_every:
                yield f": heartbeat {int(now)}\n\n"
                last_beat = now
            time.sleep(poll)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

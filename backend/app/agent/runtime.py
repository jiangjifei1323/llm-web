"""同进程有界 Worker + Agent 主循环（M1 规划/重试 + M2 HITL）。"""

from __future__ import annotations

import json
import logging
import queue
import threading
from datetime import datetime, timedelta
from typing import Any

from .. import db
from ..db.base import _now
from ..llm import chat_with_tools, get_model_name
from . import config
from .planner import build_plan_llm, replan_llm
from .tools import (
    ToolContext,
    digest,
    enabled_tools,
    execute,
    get_tool,
    needs_approval,
)

logger = logging.getLogger(__name__)

_SYSTEM = (
    "你是一名乐于助人的任务执行助手，请用简体中文回答。\n"
    "你可以调用工具获取精确信息；需要检索知识库、做日期/数值计算或经批准的联网查询时必须使用工具，不要臆造。\n"
    "请尽量按给定计划推进；若计划某步不可行，可调整工具用法后继续。\n"
    "完成任务后直接给出最终回答；若引用了知识库资料，请标注来源编号如 [1]。"
)


class TaskRunner:
    def __init__(self) -> None:
        self._queue: queue.Queue[str] = queue.Queue()
        self._workers: list[threading.Thread] = []
        self._shutdown = threading.Event()
        self._pending_retry_ids: list[str] = []

    @property
    def running(self) -> bool:
        return bool(self._workers)

    def start(self) -> None:
        if not config.AGENT_ENABLED:
            logger.warning("AGENT_ENABLED=false，Agent 执行器未启动")
            return
        if self._workers:
            return
        self._shutdown.clear()
        for i in range(config.AGENT_WORKERS):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"agent-worker-{i}",
                daemon=True,
            )
            t.start()
            self._workers.append(t)
        logger.info("Agent 执行器已启动：workers=%d", config.AGENT_WORKERS)

    def stop(self) -> None:
        self._shutdown.set()
        self._workers.clear()

    def submit(self, task_id: str) -> bool:
        if not self.running:
            return False
        self._queue.put(task_id)
        return True

    def recover_stale_tasks(self) -> int:
        self._expire_approvals()
        stale = db.list_created_or_running_tasks()
        retried = 0
        failed = 0
        for snap in stale:
            task_id = snap["id"]
            retry_count = int(snap.get("retry_count") or 0)
            if snap.get("cancel_requested"):
                db.mark_cancelled(task_id, "服务重启前已请求取消")
                db.append_event(task_id, "task.cancelled", {"reason": "cancel_on_restart"})
                failed += 1
                continue
            if retry_count < config.AGENT_MAX_RETRIES:
                db.update_task(
                    task_id,
                    status="created",
                    retry_count=retry_count + 1,
                    error=None,
                )
                db.append_event(
                    task_id,
                    "task.resumed",
                    {
                        "reason": "worker_restart",
                        "retry_count": retry_count + 1,
                        "max_retries": config.AGENT_MAX_RETRIES,
                    },
                )
                retried += 1
            else:
                db.update_task(
                    task_id,
                    status="failed",
                    error=f"服务重启后重试次数已达上限（{config.AGENT_MAX_RETRIES}），任务终止",
                )
                db.append_event(
                    task_id,
                    "task.failed",
                    {"error": "服务重启后重试次数已达上限", "retry_count": retry_count},
                )
                failed += 1
        if stale:
            logger.warning("遗留 Agent 任务：将重试 %d 个，终止 %d 个", retried, failed)
        self._pending_retry_ids = [
            s["id"]
            for s in stale
            if (s.get("retry_count") or 0) < config.AGENT_MAX_RETRIES
            and not s.get("cancel_requested")
        ]
        return retried

    def _expire_approvals(self) -> int:
        if config.HITL_TIMEOUT_MINUTES <= 0:
            return 0
        n = 0
        for snap in db.list_awaiting_approval_tasks():
            pending = snap.get("pending_approval") or {}
            requested_at = pending.get("requested_at")
            if not requested_at:
                continue
            try:
                ts = datetime.strptime(requested_at, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if _now() - ts >= timedelta(minutes=config.HITL_TIMEOUT_MINUTES):
                db.update_task(
                    snap["id"],
                    status="failed",
                    error="人工审批超时",
                    pending_approval_json=None,
                )
                db.append_event(
                    snap["id"],
                    "task.failed",
                    {"error": "人工审批超时"},
                )
                n += 1
        return n

    def enqueue_recovered(self) -> int:
        ids = self._pending_retry_ids
        self._pending_retry_ids = []
        n = 0
        for task_id in ids:
            task = db.get_task(task_id)
            if task and task.status == "created" and self.submit(task_id):
                n += 1
        if n:
            logger.info("已重新入队 %d 个 Agent 任务", n)
        return n

    def _worker_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                task_id = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.run_task(task_id)
            except Exception:  # noqa: BLE001
                logger.exception("worker 执行任务 %s 时抛出未捕获异常", task_id)
            finally:
                self._queue.task_done()

    def run_task(self, task_id: str) -> None:
        task = db.get_task(task_id)
        if task is None:
            return
        if task.status not in ("created", "running"):
            return

        checkpoint = db.get_checkpoint(task_id)
        # 审批恢复：checkpoint 带 resolution
        if checkpoint and checkpoint.get("approval_resolution"):
            db.update_task(task_id, status="running")
            db.append_event(
                task_id,
                "task.resumed",
                {"reason": "approval_resolved", "model": get_model_name()},
            )
            try:
                self._resume_after_approval(task_id, task, checkpoint)
            except Exception as exc:  # noqa: BLE001
                logger.exception("任务 %s 审批恢复失败", task_id)
                db.update_task(task_id, status="failed", error=str(exc))
                db.append_event(task_id, "task.failed", {"error": str(exc)})
            return

        db.update_task(task_id, status="running")
        db.append_event(
            task_id,
            "task.started",
            {
                "model": get_model_name(),
                "retry_count": getattr(task, "retry_count", 0) or 0,
            },
        )

        try:
            # 全新执行时清掉陈旧 checkpoint
            db.set_checkpoint(task_id, None)
            db.set_pending_approval(task_id, None)

            kb_ids = _parse_kb_ids(task.kb_ids_json)
            plan_usage: dict[str, Any] = {}
            plan, plan_source = build_plan_llm(
                task.input, kb_ids, usage_out=plan_usage
            )
            self._account_plan_llm(task_id, task, plan_usage)
            self._persist_plan(task_id, plan, source=plan_source, revised=False)

            state = {
                "plan": plan,
                "kb_ids": kb_ids,
                "collected_sources": [],
                "observations": [],
                "total_usage": {
                    "input_tokens": int(plan_usage.get("input_tokens") or 0),
                    "output_tokens": int(plan_usage.get("output_tokens") or 0),
                    "total_tokens": int(plan_usage.get("total_tokens") or 0),
                },
                "replan_round": 0,
                "answer": "",
            }
            self._run_outer_loop(task_id, task, state)
        except _PausedForApproval:
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("任务 %s 执行失败", task_id)
            db.update_task(task_id, status="failed", error=str(exc))
            db.append_event(task_id, "task.failed", {"error": str(exc)})

    def _resume_after_approval(
        self, task_id: str, task: Any, checkpoint: dict[str, Any]
    ) -> None:
        resolution = checkpoint.get("approval_resolution") or {}
        action = resolution.get("action")
        current = checkpoint.get("current_call") or {}
        messages = list(checkpoint.get("messages") or [])
        plan = list(checkpoint.get("plan") or [])
        kb_ids = list(checkpoint.get("kb_ids") or [])
        remaining = list(checkpoint.get("remaining_calls") or [])
        collected_sources = list(checkpoint.get("collected_sources") or [])
        observations = list(checkpoint.get("observations") or [])
        total_usage = dict(
            checkpoint.get("total_usage")
            or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        )
        replan_round = int(checkpoint.get("replan_round") or 0)
        tool_fail_count = int(checkpoint.get("tool_fail_count") or 0)
        tool_call_count = int(checkpoint.get("tool_call_count") or 0)
        seen_signatures = set(checkpoint.get("seen_signatures") or [])
        tools_used = set(checkpoint.get("tools_used") or [])

        user = db.get_user_by_id(task.user_id)
        ctx = ToolContext(
            user_id=task.user_id,
            task_id=task_id,
            conversation_id=task.conversation_id,
            kb_ids=kb_ids,
            is_admin=bool(user.is_admin) if user else False,
        )

        # 处理当前待审批调用
        call_id = str(current.get("id") or "call_0")
        name = str(current.get("name") or "")
        args = resolution.get("args") if action == "edit" else current.get("args")
        if not isinstance(args, dict):
            args = dict(current.get("args") or {})

        if action == "reject":
            obs = json.dumps(
                {
                    "error": "rejected",
                    "message": resolution.get("reason") or "用户拒绝执行该工具",
                },
                ensure_ascii=False,
            )
            tool_fail_count += 1
            db.log_tool_call(
                task_id=task_id,
                tool_name=name,
                args=args,
                observation=obs,
                ok=False,
                risk=(get_tool(name).risk if get_tool(name) else "external"),
                latency_ms=0,
                step_key=_match_step_key(plan, name),
            )
            snap = db.get_task(task_id)
            db.update_task(
                task_id,
                used_tool_calls=(snap.used_tool_calls if snap else 0) + 1,
            )
            db.append_event(
                task_id,
                "tool.finished",
                {
                    "tool": name,
                    "ok": False,
                    "summary": digest(obs),
                    "tool_call_id": call_id,
                    "rejected": True,
                },
            )
            observations.append(f"{name}: 用户拒绝")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": obs,
                }
            )
        else:
            # approve / edit
            db.append_event(
                task_id,
                "tool.started",
                {"tool": name, "args": args, "tool_call_id": call_id, "approved": True},
            )
            result = execute(name, ctx, args, approved=True)
            tool_call_count += 1
            tools_used.add(name)
            if not result.ok:
                tool_fail_count += 1
            db.log_tool_call(
                task_id=task_id,
                tool_name=name,
                args=args,
                observation=result.observation,
                ok=result.ok,
                risk=(get_tool(name).risk if get_tool(name) else "external"),
                latency_ms=result.latency_ms,
                step_key=_match_step_key(plan, name),
            )
            snap = db.get_task(task_id)
            db.update_task(
                task_id,
                used_tool_calls=(snap.used_tool_calls if snap else 0) + 1,
            )
            summary = digest(result.observation)
            observations.append(f"{name}: {summary}")
            db.append_event(
                task_id,
                "tool.finished",
                {
                    "tool": name,
                    "ok": result.ok,
                    "summary": summary,
                    "tool_call_id": call_id,
                    "latency_ms": result.latency_ms,
                    "approved": True,
                },
            )
            if result.data and isinstance(result.data.get("sources"), list):
                collected_sources.extend(result.data["sources"])
            if result.data and isinstance(result.data.get("results"), list):
                # web_search 结果也可记入观察，无需进 sources
                pass
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": result.observation,
                }
            )

        # 继续执行同批剩余工具（可能再次挂起）
        try:
            cont = self._continue_tool_batch(
                task_id,
                task,
                ctx,
                plan,
                messages,
                remaining,
                collected_sources,
                observations,
                tool_fail_count,
                tool_call_count,
                seen_signatures,
                tools_used,
                total_usage,
                replan_round,
                kb_ids,
            )
        except _PausedForApproval:
            return

        collected_sources = cont["collected_sources"]
        observations = cont["observations"]
        messages = cont["messages"]
        tool_fail_count = cont["tool_fail_count"]
        tool_call_count = cont["tool_call_count"]
        seen_signatures = cont["seen_signatures"]
        tools_used = cont["tools_used"]

        # 清掉审批检查点，继续 tool loop
        db.set_checkpoint(task_id, None)
        db.set_pending_approval(task_id, None)

        try:
            loop_answer, sources, usage, meta = self._tool_loop(
                task_id,
                task,
                kb_ids,
                plan,
                messages=messages,
                seed_meta={
                    "tool_fail_count": tool_fail_count,
                    "tool_call_count": tool_call_count,
                    "seen_signatures": seen_signatures,
                    "tools_used": tools_used,
                    "observations": observations,
                    "collected_sources": collected_sources,
                },
                replan_round=replan_round,
                outer_usage=total_usage,
            )
        except _PausedForApproval:
            return

        total_usage["input_tokens"] += usage.get("input_tokens", 0)
        total_usage["output_tokens"] += usage.get("output_tokens", 0)
        total_usage["total_tokens"] += usage.get("total_tokens", 0)
        collected_sources.extend(sources)
        observations.extend(meta.get("observations") or [])

        state = {
            "plan": plan,
            "kb_ids": kb_ids,
            "collected_sources": collected_sources,
            "observations": observations,
            "total_usage": total_usage,
            "replan_round": replan_round,
            "answer": loop_answer,
            "last_meta": meta,
        }
        try:
            self._run_outer_loop(task_id, task, state, skip_first_tool_loop=True)
        except _PausedForApproval:
            return

    def _run_outer_loop(
        self,
        task_id: str,
        task: Any,
        state: dict[str, Any],
        *,
        skip_first_tool_loop: bool = False,
    ) -> None:
        plan = state["plan"]
        kb_ids = state["kb_ids"]
        collected_sources: list = state["collected_sources"]
        observations: list = state["observations"]
        total_usage: dict = state["total_usage"]
        replan_round = int(state["replan_round"])
        answer = state.get("answer") or ""
        last_meta = state.get("last_meta") or {
            "tool_fail_count": 0,
            "tool_call_count": 0,
            "repeated_tool": False,
            "observations": [],
        }

        first = True
        while True:
            if self._should_stop(task_id):
                self._fail_or_cancel(task_id)
                return

            if not (first and skip_first_tool_loop):
                loop_answer, sources, usage, meta = self._tool_loop(
                    task_id, task, kb_ids, plan
                )
                total_usage["input_tokens"] += usage.get("input_tokens", 0)
                total_usage["output_tokens"] += usage.get("output_tokens", 0)
                total_usage["total_tokens"] += usage.get("total_tokens", 0)
                collected_sources.extend(sources)
                observations.extend(meta.get("observations") or [])
                answer = loop_answer
                last_meta = meta
            first = False

            if self._should_stop(task_id):
                self._fail_or_cancel(task_id)
                return

            need_replan, reason = self._should_replan(last_meta, answer, replan_round)
            if not need_replan:
                break

            replan_round += 1
            snap = db.get_task(task_id)
            db.update_task(
                task_id,
                replan_count=(getattr(snap, "replan_count", 0) or 0) + 1,
            )
            replan_usage: dict[str, Any] = {}
            plan, plan_source = replan_llm(
                task.input,
                kb_ids,
                plan,
                reason,
                observations,
                usage_out=replan_usage,
            )
            self._account_plan_llm(task_id, task, replan_usage)
            total_usage["input_tokens"] += int(replan_usage.get("input_tokens") or 0)
            total_usage["output_tokens"] += int(replan_usage.get("output_tokens") or 0)
            total_usage["total_tokens"] += int(replan_usage.get("total_tokens") or 0)
            self._persist_plan(
                task_id, plan, source=plan_source, revised=True, reason=reason
            )
            state["plan"] = plan

        self._complete_task(
            task_id, task, plan, answer, collected_sources, total_usage
        )

    def _complete_task(
        self,
        task_id: str,
        task: Any,
        plan: list[dict],
        answer: str,
        collected_sources: list[dict],
        total_usage: dict[str, int],
    ) -> None:
        db.set_checkpoint(task_id, None)
        db.set_pending_approval(task_id, None)
        if answer:
            db.append_event(task_id, "message.delta", {"delta": answer})
        unique_sources = _dedupe_sources(collected_sources)
        message_id = db.save_chat_round_with_id(
            task.conversation_id,
            task.input,
            answer,
            get_model_name(),
            user_id=task.user_id,
            input_tokens=total_usage["input_tokens"],
            output_tokens=total_usage["output_tokens"],
            total_tokens=total_usage["total_tokens"],
            sources=unique_sources if unique_sources else None,
        )
        snap = db.get_task(task_id)
        db.update_task(
            task_id,
            status="succeeded",
            result_message_id=message_id,
            used_tokens=total_usage["total_tokens"],
        )
        db.append_event(
            task_id,
            "task.completed",
            {
                "message_id": message_id,
                "input_tokens": total_usage["input_tokens"],
                "output_tokens": total_usage["output_tokens"],
                "total_tokens": total_usage["total_tokens"],
                "replan_count": getattr(snap, "replan_count", 0) if snap else 0,
            },
        )
        for step in plan:
            if step.get("kind") == "final":
                self._emit_step(
                    task_id,
                    step,
                    "succeeded",
                    output_summary=digest(answer or "", 200),
                )

    def _pause_for_approval(
        self,
        task_id: str,
        *,
        plan: list[dict],
        messages: list[dict],
        kb_ids: list[int],
        current_call: dict,
        remaining_calls: list[dict],
        collected_sources: list,
        observations: list,
        total_usage: dict,
        replan_round: int,
        tool_fail_count: int,
        tool_call_count: int,
        seen_signatures: set[str],
        tools_used: set[str],
    ) -> None:
        tool = get_tool(str(current_call.get("name") or ""))
        risk = tool.risk if tool else "external"
        pending = {
            "tool": current_call.get("name"),
            "args": current_call.get("args") or {},
            "tool_call_id": current_call.get("id"),
            "risk": risk,
            "requested_at": _now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        checkpoint = {
            "plan": plan,
            "messages": messages,
            "kb_ids": kb_ids,
            "current_call": current_call,
            "remaining_calls": remaining_calls,
            "collected_sources": collected_sources,
            "observations": observations,
            "total_usage": total_usage,
            "replan_round": replan_round,
            "tool_fail_count": tool_fail_count,
            "tool_call_count": tool_call_count,
            "seen_signatures": list(seen_signatures),
            "tools_used": list(tools_used),
            "approval_resolution": None,
        }
        db.set_checkpoint(task_id, checkpoint)
        db.set_pending_approval(task_id, pending)
        db.update_task(task_id, status="awaiting_approval")
        db.append_event(task_id, "approval.required", pending)
        raise _PausedForApproval()

    def _continue_tool_batch(
        self,
        task_id: str,
        task: Any,
        ctx: ToolContext,
        plan: list[dict],
        messages: list[dict],
        remaining: list[dict],
        collected_sources: list,
        observations: list,
        tool_fail_count: int,
        tool_call_count: int,
        seen_signatures: set[str],
        tools_used: set[str],
        total_usage: dict,
        replan_round: int,
        kb_ids: list[int],
    ) -> dict[str, Any]:
        i = 0
        while i < len(remaining):
            if self._should_stop(task_id):
                break
            call = remaining[i]
            name = str(call.get("name") or "")
            args = dict(call.get("args") or {})
            call_id = str(call.get("id") or f"call_{i}")
            if needs_approval(name):
                self._pause_for_approval(
                    task_id,
                    plan=plan,
                    messages=messages,
                    kb_ids=kb_ids,
                    current_call={"id": call_id, "name": name, "args": args},
                    remaining_calls=remaining[i + 1 :],
                    collected_sources=collected_sources,
                    observations=observations,
                    total_usage=total_usage,
                    replan_round=replan_round,
                    tool_fail_count=tool_fail_count,
                    tool_call_count=tool_call_count,
                    seen_signatures=seen_signatures,
                    tools_used=tools_used,
                )
            sig = f"{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"
            seen_signatures.add(sig)
            db.append_event(
                task_id,
                "tool.started",
                {"tool": name, "args": args, "tool_call_id": call_id},
            )
            result = execute(name, ctx, args, approved=False)
            tool_call_count += 1
            tools_used.add(name)
            if not result.ok:
                tool_fail_count += 1
            risk = get_tool(name).risk if get_tool(name) else "read"
            db.log_tool_call(
                task_id=task_id,
                tool_name=name,
                args=args,
                observation=result.observation,
                ok=result.ok,
                risk=risk,
                latency_ms=result.latency_ms,
                step_key=_match_step_key(plan, name),
            )
            snap = db.get_task(task_id)
            db.update_task(
                task_id,
                used_tool_calls=(snap.used_tool_calls if snap else 0) + 1,
            )
            summary = digest(result.observation)
            observations.append(f"{name}: {summary}")
            db.append_event(
                task_id,
                "tool.finished",
                {
                    "tool": name,
                    "ok": result.ok,
                    "summary": summary,
                    "tool_call_id": call_id,
                    "latency_ms": result.latency_ms,
                },
            )
            if result.data and isinstance(result.data.get("sources"), list):
                collected_sources.extend(result.data["sources"])
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": result.observation,
                }
            )
            i += 1
        return {
            "messages": messages,
            "collected_sources": collected_sources,
            "observations": observations,
            "tool_fail_count": tool_fail_count,
            "tool_call_count": tool_call_count,
            "seen_signatures": seen_signatures,
            "tools_used": tools_used,
        }

    def _account_plan_llm(
        self, task_id: str, task: Any, usage: dict[str, Any]
    ) -> None:
        if not usage:
            return
        inp = int(usage.get("input_tokens") or 0)
        out = int(usage.get("output_tokens") or 0)
        total = int(usage.get("total_tokens") or (inp + out))
        snap = db.get_task(task_id)
        db.update_task(
            task_id,
            used_llm_calls=(snap.used_llm_calls if snap else 0) + 1,
            used_tokens=(snap.used_tokens if snap else 0) + total,
        )
        db.log_llm_call(
            task.conversation_id,
            task.user_id,
            str(usage.get("model") or get_model_name()),
            [
                {"role": "system", "content": "planner"},
                {"role": "user", "content": task.input},
            ],
            str(usage.get("raw") or ""),
            inp,
            out,
            total,
            usage.get("latency_ms"),
            success=True,
            task_id=task_id,
        )

    def _persist_plan(
        self,
        task_id: str,
        plan: list[dict],
        *,
        source: str,
        revised: bool,
        reason: str | None = None,
    ) -> None:
        db.update_task(task_id, plan_json=json.dumps(plan, ensure_ascii=False))
        payload: dict[str, Any] = {
            "plan": plan,
            "source": source,
            "revised": revised,
        }
        if reason:
            payload["reason"] = reason
        db.append_event(task_id, "plan.updated", payload)
        for step in plan:
            self._emit_step(task_id, step, "pending")

    def _emit_step(
        self,
        task_id: str,
        step: dict,
        status: str,
        output_summary: str | None = None,
        input_summary: str | None = None,
    ) -> None:
        step_key = str(step.get("id") or "step")
        title = str(step.get("title") or step_key)
        db.upsert_step(
            task_id=task_id,
            step_key=step_key,
            title=title,
            status=status,
            input_summary=input_summary,
            output_summary=output_summary,
            mark_started=status == "running",
            mark_finished=status in ("succeeded", "failed", "skipped"),
        )
        if status == "pending":
            return
        event = "step.started" if status == "running" else "step.finished"
        payload: dict[str, Any] = {
            "step_id": step_key,
            "title": title,
            "status": status,
        }
        if output_summary:
            payload["summary"] = output_summary
        db.append_event(task_id, event, payload)
        if status == "running":
            snap = db.get_task(task_id)
            db.update_task(task_id, used_steps=(snap.used_steps if snap else 0) + 1)

    def _should_stop(self, task_id: str) -> bool:
        task = db.get_task(task_id)
        if task is None:
            return True
        if task.cancel_requested or task.status == "cancelled":
            return True
        if task.deadline_at and _now() >= task.deadline_at:
            db.update_task(task_id, status="failed", error="任务超过截止时间")
            db.append_event(task_id, "task.failed", {"error": "任务超过截止时间"})
            return True
        if task.used_llm_calls >= config.AGENT_MAX_LLM_CALLS:
            return True
        if task.used_tool_calls >= config.AGENT_MAX_TOOL_CALLS:
            return True
        if task.used_tokens >= config.AGENT_MAX_TOKENS:
            return True
        if task.used_steps >= config.AGENT_MAX_STEPS + 4:
            return True
        return False

    def _fail_or_cancel(self, task_id: str) -> None:
        task = db.get_task(task_id)
        if task is None:
            return
        if task.cancel_requested or task.status == "cancelled":
            db.mark_cancelled(task_id)
            db.set_pending_approval(task_id, None)
            db.append_event(task_id, "task.cancelled", {"reason": "user_cancel"})
            return
        if task.status in ("succeeded", "failed", "cancelled"):
            return
        reason = "超出执行预算"
        if task.used_llm_calls >= config.AGENT_MAX_LLM_CALLS:
            reason = f"模型调用达到上限 {config.AGENT_MAX_LLM_CALLS}"
        elif task.used_tool_calls >= config.AGENT_MAX_TOOL_CALLS:
            reason = f"工具调用达到上限 {config.AGENT_MAX_TOOL_CALLS}"
        elif task.used_tokens >= config.AGENT_MAX_TOKENS:
            reason = f"Token 用量达到上限 {config.AGENT_MAX_TOKENS}"
        db.update_task(task_id, status="failed", error=reason)
        db.append_event(task_id, "task.failed", {"error": reason})

    def _should_replan(
        self, meta: dict[str, Any], answer: str, replan_round: int
    ) -> tuple[bool, str]:
        if replan_round >= config.AGENT_MAX_REPLANS:
            return False, ""
        fail_count = int(meta.get("tool_fail_count") or 0)
        tool_calls = int(meta.get("tool_call_count") or 0)
        repeated = bool(meta.get("repeated_tool"))
        weak = (
            (not answer)
            or answer.startswith("任务未在预算内")
            or answer == "未能生成回答。"
        )
        if repeated:
            return True, "检测到重复无效的工具调用，需要调整计划"
        if fail_count >= 2:
            return True, f"连续/多次工具失败（{fail_count} 次），需要重规划"
        if fail_count >= 1 and weak:
            return True, "工具调用失败且未得到有效最终回答"
        if tool_calls == 0 and weak:
            return True, "未调用工具且回答不足，尝试更明确的计划"
        return False, ""

    def _tool_loop(
        self,
        task_id: str,
        task: Any,
        kb_ids: list[int],
        plan: list[dict],
        *,
        messages: list[dict[str, Any]] | None = None,
        seed_meta: dict[str, Any] | None = None,
        replan_round: int = 0,
        outer_usage: dict[str, int] | None = None,
    ) -> tuple[str, list[dict], dict[str, int], dict[str, Any]]:
        tools = enabled_tools()
        tool_schemas = [t.as_openai_tool() for t in tools]
        user = db.get_user_by_id(task.user_id)
        ctx = ToolContext(
            user_id=task.user_id,
            task_id=task_id,
            conversation_id=task.conversation_id,
            kb_ids=kb_ids,
            is_admin=bool(user.is_admin) if user else False,
        )

        if messages is None:
            history = db.load_conversation_messages(task.conversation_id)[-20:]
            plan_hint = "；".join(f"{s.get('id')}:{s.get('title')}" for s in plan)
            messages = [
                {"role": "system", "content": _SYSTEM + f"\n当前计划：{plan_hint}"},
                *history,
                {"role": "user", "content": task.input},
            ]

        for step in plan:
            if step.get("kind") == "tool":
                self._emit_step(task_id, step, "running")

        seed = seed_meta or {}
        collected_sources: list[dict] = list(seed.get("collected_sources") or [])
        observations: list[str] = list(seed.get("observations") or [])
        total_in = 0
        total_out = 0
        final_answer = ""
        tool_fail_count = int(seed.get("tool_fail_count") or 0)
        tool_call_count = int(seed.get("tool_call_count") or 0)
        seen_signatures: set[str] = set(seed.get("seen_signatures") or [])
        repeated_tool = False
        tools_used: set[str] = set(seed.get("tools_used") or [])
        usage_bucket = outer_usage or {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }

        for _ in range(config.AGENT_MAX_LLM_CALLS):
            if self._should_stop(task_id):
                break

            result = chat_with_tools(messages, tool_schemas)
            total_in += result.usage.input_tokens
            total_out += result.usage.output_tokens
            snap = db.get_task(task_id)
            db.update_task(
                task_id,
                used_llm_calls=(snap.used_llm_calls if snap else 0) + 1,
                used_tokens=(snap.used_tokens if snap else 0)
                + result.usage.total_tokens,
            )
            db.log_llm_call(
                task.conversation_id,
                task.user_id,
                result.model,
                messages,
                result.answer
                or json.dumps(
                    [c.__dict__ for c in result.tool_calls], ensure_ascii=False
                ),
                result.usage.input_tokens,
                result.usage.output_tokens,
                result.usage.total_tokens,
                result.latency_ms,
                success=True,
                task_id=task_id,
            )

            if not result.tool_calls:
                final_answer = (result.answer or "").strip()
                break

            call_dicts = [
                {
                    "id": c.id or f"call_{i}",
                    "name": c.name,
                    "args": c.args,
                }
                for i, c in enumerate(result.tool_calls)
            ]
            messages.append(
                {
                    "role": "assistant",
                    "content": result.answer or "",
                    "tool_calls": [
                        {**c, "type": "tool_call"} for c in call_dicts
                    ],
                }
            )

            # 逐个执行；遇需审批则挂起
            for idx, call in enumerate(call_dicts):
                if self._should_stop(task_id):
                    break
                name = call["name"]
                args = dict(call.get("args") or {})
                call_id = call["id"]
                if needs_approval(name):
                    self._pause_for_approval(
                        task_id,
                        plan=plan,
                        messages=messages,
                        kb_ids=kb_ids,
                        current_call={"id": call_id, "name": name, "args": args},
                        remaining_calls=call_dicts[idx + 1 :],
                        collected_sources=collected_sources,
                        observations=observations,
                        total_usage={
                            "input_tokens": usage_bucket.get("input_tokens", 0)
                            + total_in,
                            "output_tokens": usage_bucket.get("output_tokens", 0)
                            + total_out,
                            "total_tokens": usage_bucket.get("total_tokens", 0)
                            + total_in
                            + total_out,
                        },
                        replan_round=replan_round,
                        tool_fail_count=tool_fail_count,
                        tool_call_count=tool_call_count,
                        seen_signatures=seen_signatures,
                        tools_used=tools_used,
                    )

                sig = f"{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"
                if sig in seen_signatures:
                    repeated_tool = True
                seen_signatures.add(sig)

                step_key = _match_step_key(plan, name)
                db.append_event(
                    task_id,
                    "tool.started",
                    {
                        "tool": name,
                        "args": args,
                        "tool_call_id": call_id,
                        "step_id": step_key,
                    },
                )
                tool_result = execute(name, ctx, args, approved=False)
                tool_call_count += 1
                tools_used.add(name)
                if not tool_result.ok:
                    tool_fail_count += 1
                risk = get_tool(name).risk if get_tool(name) else "read"
                db.log_tool_call(
                    task_id=task_id,
                    tool_name=name,
                    args=args,
                    observation=tool_result.observation,
                    ok=tool_result.ok,
                    risk=risk,
                    latency_ms=tool_result.latency_ms,
                    step_key=step_key,
                )
                snap = db.get_task(task_id)
                db.update_task(
                    task_id,
                    used_tool_calls=(snap.used_tool_calls if snap else 0) + 1,
                )
                summary = digest(tool_result.observation)
                observations.append(f"{name}: {summary}")
                db.append_event(
                    task_id,
                    "tool.finished",
                    {
                        "tool": name,
                        "ok": tool_result.ok,
                        "summary": summary,
                        "tool_call_id": call_id,
                        "latency_ms": tool_result.latency_ms,
                        "step_id": step_key,
                    },
                )
                if tool_result.data and isinstance(
                    tool_result.data.get("sources"), list
                ):
                    collected_sources.extend(tool_result.data["sources"])
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": tool_result.observation,
                    }
                )
        else:
            if not final_answer:
                final_answer = "任务未在预算内完成，请缩小问题范围后重试。"

        if not final_answer:
            final_answer = "未能生成回答。"

        for step in plan:
            if step.get("kind") != "tool":
                continue
            step_tools = set(step.get("tools") or [])
            if step_tools & tools_used:
                self._emit_step(
                    task_id, step, "succeeded", output_summary="已执行相关工具"
                )
            elif tool_fail_count and step_tools:
                self._emit_step(
                    task_id, step, "failed", output_summary="相关工具未成功完成"
                )
            else:
                self._emit_step(
                    task_id, step, "skipped", output_summary="本轮未执行该步骤工具"
                )

        return (
            final_answer,
            _dedupe_sources(collected_sources)[:8],
            {
                "input_tokens": total_in,
                "output_tokens": total_out,
                "total_tokens": total_in + total_out,
            },
            {
                "tool_fail_count": tool_fail_count,
                "tool_call_count": tool_call_count,
                "repeated_tool": repeated_tool,
                "observations": observations,
            },
        )


class _PausedForApproval(Exception):
    """内部控制流：任务已挂起等待审批。"""


def _parse_kb_ids(raw: str | None) -> list[int]:
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    return [int(x) for x in data]


def _match_step_key(plan: list[dict], tool_name: str) -> str | None:
    for step in plan:
        if tool_name in (step.get("tools") or []):
            return str(step.get("id"))
    return None


def _dedupe_sources(sources: list[dict]) -> list[dict]:
    unique: list[dict] = []
    seen: set[str] = set()
    for s in sources:
        key = f"{s.get('document_id')}:{s.get('content', '')[:80]}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(s)
    return unique


_runner = TaskRunner()


def get_runner() -> TaskRunner:
    return _runner


def start_runner() -> None:
    _runner.recover_stale_tasks()
    _runner.start()
    _runner.enqueue_recovered()


def stop_runner() -> None:
    _runner.stop()


def submit_task(task_id: str) -> bool:
    return _runner.submit(task_id)


def make_deadline():
    return _now() + timedelta(minutes=config.AGENT_DEADLINE_MINUTES)


def resolve_approval(
    task_id: str,
    *,
    action: str,
    args: dict[str, Any] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """处理人工审批并重新入队。返回任务快照字段。"""
    task = db.get_task(task_id)
    if task is None:
        raise ValueError("任务不存在")
    if task.status != "awaiting_approval":
        raise ValueError("任务当前不在等待审批状态")
    pending = db.get_pending_approval(task_id)
    checkpoint = db.get_checkpoint(task_id)
    if not pending or not checkpoint:
        raise ValueError("缺少审批上下文")

    if config.HITL_TIMEOUT_MINUTES > 0:
        requested_at = pending.get("requested_at")
        if requested_at:
            try:
                ts = datetime.strptime(str(requested_at), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                ts = None
            if ts is not None and _now() - ts >= timedelta(
                minutes=config.HITL_TIMEOUT_MINUTES
            ):
                db.update_task(
                    task_id,
                    status="failed",
                    error="人工审批超时",
                    pending_approval_json=None,
                )
                db.append_event(task_id, "task.failed", {"error": "人工审批超时"})
                raise ValueError("人工审批已超时")

    action = action.strip().lower()
    if action not in ("approve", "reject", "edit"):
        raise ValueError("action 须为 approve / reject / edit")
    if action == "edit" and not isinstance(args, dict):
        raise ValueError("edit 必须提供 args 对象")

    resolution = {
        "action": action,
        "args": args if action == "edit" else pending.get("args"),
        "reason": reason,
        "resolved_at": _now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    checkpoint["approval_resolution"] = resolution
    db.set_checkpoint(task_id, checkpoint)
    db.set_pending_approval(task_id, None)
    db.update_task(task_id, status="running")
    db.append_event(
        task_id,
        "approval.resolved",
        {
            "action": action,
            "tool": pending.get("tool"),
            "args": resolution.get("args"),
            "reason": reason,
        },
    )
    if not submit_task(task_id):
        db.update_task(task_id, status="failed", error="Agent 执行器未启动")
        raise RuntimeError("Agent 执行器未启动")
    snap = db.list_task_snapshot(task_id)
    return snap or {"id": task_id, "status": "running"}

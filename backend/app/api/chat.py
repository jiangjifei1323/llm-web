"""问答接口：非流式与 SSE 流式各一个。

基于 LangChain 调用模型，并把每次调用的输入(messages)、输出与 token 用量落库到 llm_calls。
"""

import json
from collections.abc import Iterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from .. import auth, db
from ..db import User
from ..llm import TokenUsage, chat_completion, chat_completion_stream, get_model_name
from ..schemas.chat import ChatMessage, ChatRequest, ChatResponse
from .deps import check_conversation_access

router = APIRouter(prefix="/api/chat", tags=["问答"])


def _resolve_conversation_id(req: ChatRequest) -> str:
    return req.conversation_id or db.new_conversation_id()


def _build_messages(req: ChatRequest, conversation_id: str) -> list[dict[str, str]]:
    """组装 system 指令 + 历史对话 + 当前问题。

    前端没带 history 但带了已存在的 conversation_id 时，从数据库补历史。
    """
    messages = [{"role": "system", "content": "你是一名乐于助人的问答助手，请用简体中文回答问题。"}]
    history = [m.model_dump() for m in req.history]
    if not history and req.conversation_id:
        history = db.load_conversation_messages(conversation_id)[-40:]
    messages.extend(history)
    messages.append({"role": "user", "content": req.message})
    return messages


def _sse(payload: dict) -> str:
    """序列化为一条 SSE 消息。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("", response_model=ChatResponse)
def chat(req: ChatRequest, user: User = Depends(auth.get_current_user)) -> ChatResponse:
    conversation_id = _resolve_conversation_id(req)
    check_conversation_access(conversation_id, user)
    messages = _build_messages(req, conversation_id)

    try:
        result = chat_completion(messages)
    except RuntimeError as exc:
        # 调用失败：记录输入与失败状态后继续抛出
        db.log_llm_call(
            conversation_id, user.id, get_model_name(), messages, None,
            0, 0, 0, None, success=False,
        )
        raise

    db.save_chat_round(conversation_id, req.message, result.answer, result.model, user_id=user.id)
    db.log_llm_call(
        conversation_id, user.id, result.model, messages, result.answer,
        result.usage.input_tokens, result.usage.output_tokens, result.usage.total_tokens,
        result.latency_ms, success=True,
    )
    return ChatResponse(answer=result.answer, model=result.model, conversation_id=conversation_id)


@router.post("/stream")
def chat_stream(req: ChatRequest, user: User = Depends(auth.get_current_user)) -> StreamingResponse:
    """SSE 流式问答：逐块推送 data: {"delta": ...}，结束推送 [DONE]。

    完整回答累积后与用户提问一起落库，并把本次调用的输入/输出/token 记入 llm_calls。
    """
    conversation_id = _resolve_conversation_id(req)
    check_conversation_access(conversation_id, user)
    messages = _build_messages(req, conversation_id)

    def event_gen() -> Iterator[str]:
        # 首条消息携带模型名与会话 id，供前端展示/延续会话
        yield _sse({"delta": "", "model": get_model_name(), "conversation_id": conversation_id})
        result_out: dict = {}
        try:
            for delta in chat_completion_stream(messages, result_out):
                yield _sse({"delta": delta})

            answer = result_out.get("answer", "")
            model = result_out.get("model", get_model_name())
            usage: TokenUsage = result_out.get("usage", TokenUsage())
            latency_ms = result_out.get("latency_ms")

            db.save_chat_round(conversation_id, req.message, answer, model, user_id=user.id)
            db.log_llm_call(
                conversation_id, user.id, model, messages, answer,
                usage.input_tokens, usage.output_tokens, usage.total_tokens,
                latency_ms, success=True,
            )
            yield "data: [DONE]\n\n"
        except RuntimeError as exc:
            # 流已开始、无法再改状态码，故以 error 事件下发；同时记录失败的调用
            db.log_llm_call(
                conversation_id, user.id, get_model_name(), messages, None,
                0, 0, 0, None, success=False,
            )
            yield _sse({"error": str(exc)})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

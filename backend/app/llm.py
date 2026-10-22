"""LLM 客户端：基于 LangChain（langchain-openai ChatOpenAI）调用 OpenAI 兼容模型。

支持非流式与流式；每次调用返回回答、模型名与 token 用量。未配置 LLM_API_KEY 时
退化为 mock 回答，并按字符数粗略估算 token，保证前端与落库逻辑在不接模型时依然可用。
"""

import os
import time
from collections.abc import Iterator
from dataclasses import dataclass

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

# role -> LangChain 消息类型
_ROLE_MAP: dict[str, type[BaseMessage]] = {
    "system": SystemMessage,
    "user": HumanMessage,
    "assistant": AIMessage,
}


@dataclass
class TokenUsage:
    """一次模型调用的 token 用量。"""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class ChatResult:
    """非流式调用结果：回答文本、实际模型名、token 用量与耗时（毫秒）。"""

    answer: str
    model: str
    usage: TokenUsage
    latency_ms: int


def is_configured() -> bool:
    """是否已配置大模型 API Key。"""
    return bool(os.getenv("LLM_API_KEY", "").strip())


def get_model_name() -> str:
    """当前配置的模型名；未配置 Key 时返回 mock。"""
    if not is_configured():
        return "mock"
    return os.getenv("LLM_MODEL", "gpt-4o-mini")


def _build_llm(streaming: bool) -> ChatOpenAI:
    """按环境变量构造 ChatOpenAI 实例。

    流式时开启 stream_usage，使最后一个 chunk 携带 usage_metadata。
    """
    kwargs: dict = {
        "api_key": os.getenv("LLM_API_KEY", "").strip(),
        "base_url": os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        "model": os.getenv("LLM_MODEL", "gpt-4o-mini"),
        "temperature": float(os.getenv("LLM_TEMPERATURE", "0.7")),
        "timeout": 120,
    }
    if streaming:
        kwargs["streaming"] = True
        kwargs["stream_usage"] = True
    return ChatOpenAI(**kwargs)


def _to_lc_messages(messages: list[dict[str, str]]) -> list[BaseMessage]:
    """把 {role, content} 字典序列转成 LangChain 消息对象。"""
    lc: list[BaseMessage] = []
    for m in messages:
        cls = _ROLE_MAP.get(m.get("role", "user"), HumanMessage)
        lc.append(cls(content=m.get("content", "")))
    return lc


def _content_to_text(content: object) -> str:
    """LangChain 消息 content 可能是 str 或分块列表，统一拍平为纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for part in content:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, dict):
                out.append(str(part.get("text", "")))
        return "".join(out)
    return str(content)


def _usage_from_metadata(metadata: dict | None) -> TokenUsage:
    """从 usage_metadata 提取输入/输出 token；兼容只提供 total 的情况。"""
    if not metadata:
        return TokenUsage()
    inp = int(metadata.get("input_tokens", 0) or 0)
    out = int(metadata.get("output_tokens", 0) or 0)
    total = int(metadata.get("total_tokens", 0) or 0)
    if total and not (inp or out):
        out = total
    elif total and inp and not out:
        out = max(total - inp, 0)
    return TokenUsage(input_tokens=inp, output_tokens=out)


def _estimate_usage(messages: list[dict[str, str]], answer: str) -> TokenUsage:
    """mock 模式下按字符数粗略估算 token（中文约 2 字符 ≈ 1 token）。"""
    inp = sum(len(m.get("content", "")) for m in messages) // 2
    out = len(answer) // 2
    return TokenUsage(input_tokens=max(inp, 1), output_tokens=max(out, 1))


def _mock_answer(messages: list[dict[str, str]]) -> str:
    last_user = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"),
        "",
    )
    return (
        "【mock 回答】后端尚未配置大模型 Key，这是一条占位回复。\n"
        f"你的问题：{last_user}\n"
        "请在 backend/.env 中设置 LLM_API_KEY 后重启服务以获取真实回答。"
    )


def chat_completion(messages: list[dict[str, str]]) -> ChatResult:
    """非流式问答：返回含回答、模型名与 token 用量的 ChatResult。"""
    if not is_configured():
        answer = _mock_answer(messages)
        return ChatResult(
            answer=answer,
            model="mock",
            usage=_estimate_usage(messages, answer),
            latency_ms=0,
        )

    llm = _build_llm(streaming=False)
    start = time.perf_counter()
    try:
        resp = llm.invoke(_to_lc_messages(messages))
    except Exception as exc:  # noqa: BLE001 - 统一转为面向用户的错误信息
        raise RuntimeError(f"调用大模型失败：{exc}") from exc
    latency_ms = int((time.perf_counter() - start) * 1000)

    model = (resp.response_metadata or {}).get("model_name") or get_model_name()
    return ChatResult(
        answer=_content_to_text(resp.content),
        model=model,
        usage=_usage_from_metadata(getattr(resp, "usage_metadata", None)),
        latency_ms=latency_ms,
    )


def chat_completion_stream(
    messages: list[dict[str, str]], result_out: dict
) -> Iterator[str]:
    """流式问答：逐个产出文本增量块。

    迭代结束后把完整回答、模型名、token 用量与耗时写入 result_out（供调用方落库）。
    result_out 键：answer / model / usage(TokenUsage) / latency_ms。
    mock 模式下逐字符产出并加短暂延迟，用于前端验证打字机效果。
    """
    if not is_configured():
        answer = _mock_answer(messages)
        result_out.update(
            {
                "answer": answer,
                "model": "mock",
                "usage": _estimate_usage(messages, answer),
                "latency_ms": 0,
            }
        )
        for char in answer:
            yield char
            time.sleep(0.02)
        return

    llm = _build_llm(streaming=True)
    start = time.perf_counter()
    parts: list[str] = []
    usage = TokenUsage()
    model = get_model_name()
    try:
        for chunk in llm.stream(_to_lc_messages(messages)):
            text = _content_to_text(chunk.content)
            if text:
                parts.append(text)
                yield text
            # usage 通常在最后一个 chunk 才出现
            if getattr(chunk, "usage_metadata", None):
                usage = _usage_from_metadata(chunk.usage_metadata)
            meta = getattr(chunk, "response_metadata", None) or {}
            model = meta.get("model_name") or model
    except Exception as exc:  # noqa: BLE001 - 统一转为面向用户的错误信息
        raise RuntimeError(f"调用大模型失败：{exc}") from exc

    result_out.update(
        {
            "answer": "".join(parts),
            "model": model,
            "usage": usage,
            "latency_ms": int((time.perf_counter() - start) * 1000),
        }
    )

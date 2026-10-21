"""LLM 客户端：调用 OpenAI 兼容的 chat/completions 接口（支持非流式与流式）；未配置 Key 时返回 mock 回答。"""

import json
import os
import time
import urllib.request
from collections.abc import Iterator


def is_configured() -> bool:
    """是否已配置大模型 API Key。"""
    return bool(os.getenv("LLM_API_KEY", "").strip())


def get_model_name() -> str:
    """当前配置的模型名；未配置 Key 时返回 mock。"""
    if not is_configured():
        return "mock"
    return os.getenv("LLM_MODEL", "gpt-4o-mini")


def chat_completion(messages: list[dict[str, str]]) -> tuple[str, str]:
    """返回 (回答文本, 模型名)。"""
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        return _mock_answer(messages), "mock"

    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")

    payload = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": float(os.getenv("LLM_TEMPERATURE", "0.7")),
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - 统一转为面向用户的错误信息
        raise RuntimeError(f"调用大模型失败：{exc}") from exc

    try:
        answer = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"大模型返回格式异常：{data}") from exc

    return answer, data.get("model", model)


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


def chat_completion_stream(messages: list[dict[str, str]]) -> Iterator[str]:
    """流式生成器：逐个产出回答文本增量块。

    mock 模式下逐字符产出并加短暂延迟，用于前端验证打字机效果。
    """
    if not is_configured():
        for char in _mock_answer(messages):
            yield char
            time.sleep(0.02)
        return

    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    payload = json.dumps(
        {
            "model": get_model_name(),
            "messages": messages,
            "temperature": float(os.getenv("LLM_TEMPERATURE", "0.7")),
            "stream": True,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.getenv('LLM_API_KEY', '').strip()}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            # 上游返回 SSE：逐行读取 "data: {...}"，直至 [DONE]
            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0].get("delta") or {}).get("content")
                if delta:
                    yield delta
    except Exception as exc:  # noqa: BLE001 - 统一转为面向用户的错误信息
        raise RuntimeError(f"调用大模型失败：{exc}") from exc

"""LLM 客户端：调用 OpenAI 兼容的 chat/completions 接口；未配置 Key 时返回 mock 回答。"""

import json
import os
import urllib.request


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

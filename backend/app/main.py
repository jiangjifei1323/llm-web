"""问答助手后端入口。"""

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .llm import chat_completion

# 加载 backend/.env（LLM_API_KEY 等配置）
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

app = FastAPI(title="llm-web 问答助手", version="0.1.0")

# 开发阶段放开跨域，方便前端直连调试（走 Vite 代理时并不依赖此项）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant|system)$")
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=40)


class ChatResponse(BaseModel):
    answer: str
    model: str


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    messages = [{"role": "system", "content": "你是一名乐于助人的问答助手，请用简体中文回答问题。"}]
    messages.extend(m.model_dump() for m in req.history)
    messages.append({"role": "user", "content": req.message})

    answer, model = chat_completion(messages)
    return ChatResponse(answer=answer, model=model)

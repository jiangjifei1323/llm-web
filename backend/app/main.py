"""问答助手后端入口：只做应用装配。

接口实现按功能模块放在 app/api/，数据访问在 app/db/，请求/响应模型在 app/schemas/。
"""

from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import auth, db
from .api import (
    admin_router,
    auth_router,
    chat_router,
    conversations_router,
    health_router,
)

# 加载 backend/.env（LLM_API_KEY、DATABASE_URL 等配置）
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    auth.seed_admin()  # 确保管理员账户 admin/admin 存在
    yield


app = FastAPI(title="llm-web 问答助手", version="0.1.0", lifespan=lifespan)

# 开发阶段放开跨域，方便前端直连调试（走 Vite 代理时并不依赖此项）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(conversations_router)
app.include_router(admin_router)

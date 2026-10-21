"""接口层：按功能模块拆分的路由，由 main.py 统一挂载。"""

from .admin import router as admin_router
from .auth import router as auth_router
from .chat import router as chat_router
from .conversations import router as conversations_router
from .health import router as health_router

__all__ = [
    "admin_router",
    "auth_router",
    "chat_router",
    "conversations_router",
    "health_router",
]

"""健康检查接口。"""

from fastapi import APIRouter

from .. import db

router = APIRouter(tags=["系统"])


@router.get("/api/health")
def health() -> dict[str, str]:
    try:
        db.ping()
        database = "ok"
    except Exception as exc:  # noqa: BLE001 - 健康检查需吞掉连接异常
        database = f"error: {exc}"
    return {"status": "ok", "database": database}

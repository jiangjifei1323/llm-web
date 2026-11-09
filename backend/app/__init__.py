"""问答助手后端包。

包初始化时立刻加载 backend/.env：子模块（app.auth、app.db.base 等）在模块级就读取
os.getenv，只有在这里先加载，才能保证 `from . import auth, db` 时配置已经生效。
"""

from pathlib import Path

from dotenv import load_dotenv

# backend/app/__init__.py -> parent.parent == backend/
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

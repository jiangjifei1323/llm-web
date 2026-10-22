# 问答助手后端（FastAPI）镜像
FROM python:3.13-slim

# 运行时环境变量：无缓冲输出、不写 .pyc、UTF-8（中文日志）
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1

# uv：按 uv.lock 精确复现依赖
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# 先复制依赖清单，命中构建缓存（代码改动不触发重装依赖）
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-cache

# 再复制应用代码
COPY app ./app

# 非 root 运行
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# 使用 uv 同步出的虚拟环境
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# 绑定 0.0.0.0 以便前端容器经 Docker 网络访问
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

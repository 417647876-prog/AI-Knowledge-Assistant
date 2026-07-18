FROM ghcr.io/astral-sh/uv:0.9.18 AS uv

FROM python:3.12-slim AS builder

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir "uv==0.9.18"

# 先复制锁文件，使依赖层在应用代码变更时仍可复用。
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./alembic.ini
RUN uv sync --frozen --no-dev

# 镜像只携带安全创建管理员所需的 CLI，不打包重置数据或评测脚本。
COPY backend/scripts/__init__.py backend/scripts/create_admin.py ./scripts/

FROM python:3.12-slim AS runtime

WORKDIR /app
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/home/app/.cache/huggingface

COPY --from=uv /uv /uvx /bin/
RUN groupadd --system app && useradd --system --gid app --create-home app
COPY --from=builder --chown=app:app /app /app
RUN mkdir -p /app/uploads /home/app/.cache/huggingface \
    && chown -R app:app /app/uploads /home/app

USER app

# syntax=docker/dockerfile:1
FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

COPY pyproject.toml uv.lock ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.13-slim AS runtime
RUN groupadd --system --gid 10001 rooms \
    && useradd --system --uid 10001 --gid rooms --no-create-home --shell /usr/sbin/nologin rooms \
    && mkdir /data \
    && chown rooms:rooms /data
COPY --from=builder --chown=rooms:rooms /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    ROOMS_HOST=0.0.0.0 \
    ROOMS_PORT=8765 \
    ROOMS_DB_PATH=/data/rooms.db \
    ROOMS_TOKENS_FILE=/app/config/agents.json
USER rooms
WORKDIR /app
EXPOSE 8765
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=3)"]
CMD ["python", "-m", "agent_rooms"]

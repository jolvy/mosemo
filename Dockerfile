# syntax=docker/dockerfile:1
FROM python:3.14-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=0 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock .python-version README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable

FROM python:3.14-slim-bookworm
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN groupadd --gid 10001 mosemo && useradd --uid 10001 --gid mosemo --no-create-home mosemo
COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations/ migrations/
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "mosemo.main:app", "--host", "0.0.0.0", "--port", "8000"]

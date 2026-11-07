FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.9.18 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"
WORKDIR /app
# Dependencies first (cached layer)
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/worker/pyproject.toml apps/worker/pyproject.toml
RUN uv sync --frozen --no-dev --no-install-workspace --package momentsales-api
COPY apps/api apps/api
RUN uv sync --frozen --no-dev --package momentsales-api \
 && useradd --system --uid 10001 app
USER app
EXPOSE 8000
CMD ["uvicorn", "momentsales.main:app", "--host", "0.0.0.0", "--port", "8000"]
